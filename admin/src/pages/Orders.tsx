import { useCallback, useEffect, useState } from "react";
import { api, ApiRequestError, type Envelope } from "../lib/api";
import { Can } from "../lib/auth";
import Pager from "../components/Pager";
import { money } from "./Dashboard";

interface OrderItem {
  slug: string;
  name: string;
  colour: string | null;
  qty: number;
  price_aed: number;
}
interface Address {
  name?: string | null;
  line1?: string | null;
  line2?: string | null;
  city?: string | null;
  state?: string | null;
  postal_code?: string | null;
  country?: string | null;
}
interface OrderRow {
  id: number;
  session_id: string;
  email: string | null;
  name: string | null;
  phone: string | null;
  address: Address;
  items: OrderItem[];
  amount_cents: number;
  currency: string;
  status: string;
  note: string | null;
  created_at: string | null;
}

interface StockRow {
  key: string;
  model: string;
  colour: string | null;
  in_stock: boolean;
}

const STATUSES = ["paid", "shipped", "delivered", "cancelled"] as const;
const FILTERS = ["all", ...STATUSES] as const;
const PILL: Record<string, string> = { paid: "warn", shipped: "info", delivered: "good", cancelled: "crit" };
const PAGE_SIZE = 25;

function addressLine(a: Address): string {
  return [a.line1, a.line2, a.city, a.state, a.postal_code, a.country].filter(Boolean).join(", ") || "—";
}

/** Glasses bought from the website: one row per paid Stripe checkout.
 *  The operator moves an order paid → shipped → delivered from here. */
export default function Orders() {
  const [rows, setRows] = useState<OrderRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (filter !== "all") params.set("status", filter);
      const res = await api<Envelope<OrderRow[]>>(`/api/v1/admin/orders?${params}`);
      setRows(res.data);
      setTotal(res.meta?.total ?? res.data.length);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not load orders.");
    } finally {
      setLoading(false);
    }
  }, [page, filter]);

  useEffect(() => { void load(); }, [load]);

  async function setStatus(row: OrderRow, status: string) {
    try {
      await api(`/api/v1/admin/orders/${row.id}`, { method: "PATCH", body: { status } });
      void load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Update failed.");
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Orders</h2>
          <p>Glasses bought on the website. Stripe collected the address and phone.</p>
        </div>
      </div>
      <RevenueStrip />
      <StockCard />
      <div className="toolbar">
        {FILTERS.map((f) => (
          <button key={f} className={`chip ${filter === f ? "on" : ""}`} onClick={() => { setFilter(f); setPage(1); }}>{f}</button>
        ))}
      </div>
      {error && <div className="error-text">{error}</div>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>When</th>
              <th>Customer</th>
              <th>Ship to</th>
              <th>Items</th>
              <th className="num">Qty</th>
              <th className="num">Total</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="loading">Loading…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={8} className="empty">No orders yet.</td></tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.id}</td>
                  <td className="num">{row.created_at ? new Date(row.created_at).toLocaleString() : "—"}</td>
                  <td>
                    <b>{row.name ?? "—"}</b>
                    <div style={{ color: "var(--td)", fontSize: 11 }}>{row.email ?? "—"}</div>
                    <div style={{ color: "var(--td)", fontSize: 11 }}>{row.phone ?? "—"}</div>
                  </td>
                  <td style={{ maxWidth: 260, fontSize: 12 }}>{addressLine(row.address)}</td>
                  <td style={{ fontSize: 12 }}>
                    {row.items.map((i, k) => (
                      <div key={k}>{i.qty} × {i.name}{i.colour ? ` (${i.colour})` : ""}</div>
                    ))}
                  </td>
                  <td className="num">{row.items.reduce((n, i) => n + i.qty, 0)}</td>
                  <td className="num">{money(row.amount_cents, row.currency)}</td>
                  <td>
                    <Can permission="billing.manage">
                      <select className={`status-select status-${row.status}`} value={row.status} onChange={(e) => void setStatus(row, e.target.value)} title="Changing the status emails the customer and the ops address">
                        {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </Can>
                    <span className={`pill ${PILL[row.status] ?? "muted"}`} style={{ marginLeft: 6 }}>{row.status}</span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
    </>
  );
}


/** What is on sale. A switched-off model shows "Out of stock" on the website
 *  and cannot be checked out; a switched-off colour is greyed out in its
 *  picker. Takes effect on the next page load — no restart. */
function StockCard() {
  const [rows, setRows] = useState<StockRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api<Envelope<StockRow[]>>("/api/v1/admin/stock");
      setRows(res.data);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not load stock.");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function toggle(row: StockRow) {
    setBusy(row.key);
    setError(null);
    try {
      await api(`/api/v1/admin/stock/${encodeURIComponent(row.key)}`, { method: "PUT", body: { in_stock: !row.in_stock } });
      await load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Update failed.");
    } finally {
      setBusy(null);
    }
  }

  const models = rows.filter((r) => r.colour === null);
  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div className="sub">Stock — what the website sells right now</div>
      {error && <div className="error-text">{error}</div>}
      <table>
        <thead>
          <tr><th>Model</th><th>Colours</th><th>On sale</th></tr>
        </thead>
        <tbody>
          {models.length === 0 ? (
            <tr><td colSpan={3} className="loading">Loading…</td></tr>
          ) : (
            models.map((m) => {
              const colours = rows.filter((r) => r.colour !== null && r.key.startsWith(m.key + ":"));
              return (
                <tr key={m.key}>
                  <td><b>{m.model}</b></td>
                  <td>
                    {colours.length === 0 ? <span style={{ color: "var(--td)" }}>—</span> : (
                      <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
                        {colours.map((c) => (
                          <Can key={c.key} permission="billing.manage">
                            <button
                              className={`chip${c.in_stock ? " on" : ""}`}
                              disabled={busy === c.key || !m.in_stock}
                              title={c.in_stock ? "On sale — click to mark out of stock" : "Out of stock — click to put back on sale"}
                              onClick={() => void toggle(c)}
                            >
                              {c.colour}{c.in_stock ? "" : " · out"}
                            </button>
                          </Can>
                        ))}
                      </span>
                    )}
                  </td>
                  <td>
                    <Can permission="billing.manage">
                      <button
                        className={`btn-outline btn-sm${m.in_stock ? "" : " danger"}`}
                        disabled={busy === m.key}
                        onClick={() => void toggle(m)}
                      >
                        {m.in_stock ? "On sale" : "Out of stock"}
                      </button>
                    </Can>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}


interface ShopSummary {
  orders_total: number;
  by_status: Record<string, number>;
  by_currency: { currency: string; orders: number; amount_cents: number; delivery_cents: number; units: number }[];
  over_time: { month: string; amounts: Record<string, number> }[];
}

/** Glasses money at the top of the Orders page: one pile per currency,
 *  orders by status, and the last months. */
function RevenueStrip() {
  const [s, setS] = useState<ShopSummary | null>(null);
  useEffect(() => {
    api<Envelope<ShopSummary>>("/api/v1/admin/orders/summary").then((r) => setS(r.data)).catch(() => {});
  }, []);
  if (!s) return null;
  return (
    <>
      <div className="stats">
        <div className="stat">
          <div className="label">Orders</div>
          <div className="value num">{s.orders_total}</div>
          <div style={{ fontSize: 11, color: "var(--td)", marginTop: 6 }}>
            {(["paid", "shipped", "delivered", "cancelled"] as const).map((k) => `${s.by_status[k] ?? 0} ${k}`).join(" · ")}
          </div>
        </div>
        {s.by_currency.length === 0 ? (
          <div className="stat"><div className="label">Revenue</div><div className="value num">—</div></div>
        ) : (
          s.by_currency.map((c) => (
            <div className="stat" key={c.currency}>
              <div className="label">Revenue · {c.currency}</div>
              <div className="value num">{money(c.amount_cents, c.currency)}</div>
              <div style={{ fontSize: 11, color: "var(--td)", marginTop: 6 }}>
                {c.orders} orders · {c.units} pairs{c.delivery_cents ? ` · incl. ${money(c.delivery_cents, c.currency)} delivery` : ""}
              </div>
            </div>
          ))
        )}
      </div>
      {s.over_time.length > 0 && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="sub">By month</div>
          <table>
            <thead><tr><th>Month</th>{s.by_currency.map((c) => <th key={c.currency} className="num">{c.currency}</th>)}</tr></thead>
            <tbody>
              {s.over_time.map((m) => (
                <tr key={m.month}>
                  <td className="num">{m.month}</td>
                  {s.by_currency.map((c) => <td key={c.currency} className="num">{m.amounts[c.currency] ? money(m.amounts[c.currency], c.currency) : "—"}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
