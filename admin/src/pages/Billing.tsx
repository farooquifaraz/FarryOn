import { useCallback, useEffect, useState } from "react";
import { api, ApiRequestError, type Envelope } from "../lib/api";
import Pager from "../components/Pager";

interface RevenueSummary {
  total_revenue_cents: number;
  mrr_cents: number;
  active_subscribers: number;
  revenue_by_plan: { plan: string; count: number; mrr_cents: number }[];
  revenue_over_time: { month: string; amount_cents: number }[];
}
interface SubscriptionRow {
  id: number;
  user_id: number;
  user_email: string | null;
  user_display_name: string | null;
  plan_name: string;
  status: string;
  started_at: string;
  current_period_end: string | null;
  lifetime_paid_cents: number;
}

const usd = (cents: number) => `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
const STATUS_PILL: Record<string, string> = {
  active: "good",
  trialing: "warn",
  past_due: "crit",
  canceled: "muted",
  expired: "muted",
};
const FILTERS = ["all", "active", "trialing", "past_due", "canceled"] as const;
const PAGE_SIZE = 20;

export default function Billing() {
  const [summary, setSummary] = useState<RevenueSummary | null>(null);
  const [rows, setRows] = useState<SubscriptionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<(typeof FILTERS)[number]>("all");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Envelope<RevenueSummary>>("/api/v1/admin/revenue/summary")
      .then((r) => setSummary(r.data))
      .catch((err) => setError(err instanceof ApiRequestError ? err.message : "Failed to load revenue."));
  }, []);

  const loadSubs = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (statusFilter !== "all") params.set("status", statusFilter);
      if (search) params.set("search", search);
      const res = await api<Envelope<SubscriptionRow[]>>(`/api/v1/admin/subscriptions?${params}`);
      setRows(res.data);
      setTotal(res.meta?.total ?? 0);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Failed to load subscriptions.");
    }
  }, [page, statusFilter, search]);

  useEffect(() => {
    void loadSubs();
  }, [loadSubs]);

  const maxMonth = Math.max(1, ...(summary?.revenue_over_time ?? []).map((m) => m.amount_cents));
  const maxPlanMrr = Math.max(1, ...(summary?.revenue_by_plan ?? []).map((p) => p.mrr_cents));

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Subscriptions &amp; Revenue</h2>
          <p>Who's subscribed, who isn't — and what it earns</p>
        </div>
      </div>

      {error && <div className="error-text" style={{ textAlign: "left", marginBottom: 10 }}>{error}</div>}

      {summary && (
        <>
          <div className="stats">
            <div className="stat">
              <div className="label">Total revenue</div>
              <div className="value num">{usd(summary.total_revenue_cents)}</div>
            </div>
            <div className="stat">
              <div className="label">MRR</div>
              <div className="value num">{usd(summary.mrr_cents)}</div>
            </div>
            <div className="stat">
              <div className="label">Active subscribers</div>
              <div className="value num">{summary.active_subscribers}</div>
            </div>
          </div>

          <div className="grid-2">
            <div className="card chart-wrap">
              <h4>Revenue over time</h4>
              <div className="sub">By month</div>
              {summary.revenue_over_time.length === 0 ? (
                <div className="empty">No payments yet.</div>
              ) : (
                <div className="chart">
                  {summary.revenue_over_time.map((m) => (
                    <div
                      key={m.month}
                      className="bar"
                      style={{ height: `${Math.round((m.amount_cents / maxMonth) * 100)}%` }}
                      title={usd(m.amount_cents)}
                    >
                      <span>{m.month}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="card">
              <h4>Revenue by plan</h4>
              <div className="sub">Share of MRR</div>
              {summary.revenue_by_plan.length === 0 ? (
                <div className="empty">No active subscriptions.</div>
              ) : (
                summary.revenue_by_plan.map((p) => (
                  <div className="plan-row" key={p.plan}>
                    <span className="name">{p.plan}</span>
                    <div className="bar-bg">
                      <div className="bar-fg" style={{ width: `${Math.round((p.mrr_cents / maxPlanMrr) * 100)}%` }} />
                    </div>
                    <span className="amt">{usd(p.mrr_cents)}/mo</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}

      <div className="toolbar">
        <input
          type="search"
          placeholder="Search subscriber email"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        {FILTERS.map((f) => (
          <button
            key={f}
            className={`chip${statusFilter === f ? " on" : ""}`}
            onClick={() => {
              setStatusFilter(f);
              setPage(1);
            }}
          >
            {f.replace("_", " ")}
          </button>
        ))}
      </div>

      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Plan</th>
              <th>Status</th>
              <th>Renews</th>
              <th>Lifetime paid</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={5} className="empty">No subscriptions match.</td></tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <b>{row.user_display_name ?? "—"}</b>
                    <div style={{ color: "var(--td)", fontSize: 11 }}>{row.user_email}</div>
                  </td>
                  <td>{row.plan_name}</td>
                  <td><span className={`pill ${STATUS_PILL[row.status] ?? "muted"}`}>{row.status.replace("_", " ")}</span></td>
                  <td className="num">
                    {row.current_period_end ? new Date(row.current_period_end).toLocaleDateString() : "—"}
                  </td>
                  <td className="num">{usd(row.lifetime_paid_cents)}</td>
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
