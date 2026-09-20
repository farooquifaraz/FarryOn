import { useCallback, useEffect, useState } from "react";
import { api, ApiRequestError, type Envelope } from "../lib/api";
import { Can } from "../lib/auth";
import Pager from "../components/Pager";

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

const STATUSES = ["paid", "shipped", "delivered", "cancelled"] as const;
const FILTERS = ["all", ...STATUSES] as const;
const PILL: Record<string, string> = { paid: "warn", shipped: "good", delivered: "good", cancelled: "muted" };
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
              <th className="num">Total</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="loading">Loading…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={7} className="empty">No orders yet.</td></tr>
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
                  <td className="num">{row.currency} {(row.amount_cents / 100).toFixed(0)}</td>
                  <td>
                    <Can permission="billing.manage">
                      <select value={row.status} onChange={(e) => void setStatus(row, e.target.value)}>
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
