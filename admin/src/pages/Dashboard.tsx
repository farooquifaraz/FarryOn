/** Overview KPIs assembled client-side from existing endpoints — a
 * dedicated /admin/dashboard/stats endpoint can replace this later. */

import { useEffect, useState } from "react";
import { api, type Envelope } from "../lib/api";
import { useAuth } from "../lib/auth";

interface RevenueSummary {
  total_revenue_cents: number;
  mrr_cents: number;
  active_subscribers: number;
  revenue_over_time: { month: string; amount_cents: number }[];
}
interface AuditRow {
  id: number;
  actor_id: number | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  created_at: string;
}

const usd = (cents: number) => `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;

export default function Dashboard() {
  const { can, user } = useAuth();
  const [userTotal, setUserTotal] = useState<number | null>(null);
  const [revenue, setRevenue] = useState<RevenueSummary | null>(null);
  const [recent, setRecent] = useState<AuditRow[]>([]);

  useEffect(() => {
    if (can("users.read"))
      api<Envelope<unknown[]>>("/api/v1/users?page_size=1").then((r) => setUserTotal(r.meta?.total ?? 0)).catch(() => {});
    if (can("billing.read"))
      api<Envelope<RevenueSummary>>("/api/v1/admin/revenue/summary").then((r) => setRevenue(r.data)).catch(() => {});
    if (can("audit.read"))
      api<Envelope<AuditRow[]>>("/api/v1/audit-logs?page_size=8").then((r) => setRecent(r.data)).catch(() => {});
  }, [can]);

  const maxMonth = Math.max(1, ...(revenue?.revenue_over_time ?? []).map((m) => m.amount_cents));

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Dashboard</h2>
          <p>Welcome back, {user?.display_name ?? user?.email}</p>
        </div>
      </div>

      <div className="stats">
        {userTotal !== null && (
          <div className="stat">
            <div className="label">Total users</div>
            <div className="value num">{userTotal.toLocaleString()}</div>
          </div>
        )}
        {revenue && (
          <>
            <div className="stat">
              <div className="label">Total revenue</div>
              <div className="value num">{usd(revenue.total_revenue_cents)}</div>
            </div>
            <div className="stat">
              <div className="label">MRR</div>
              <div className="value num">{usd(revenue.mrr_cents)}</div>
            </div>
            <div className="stat">
              <div className="label">Active subscribers</div>
              <div className="value num">{revenue.active_subscribers}</div>
            </div>
          </>
        )}
      </div>

      {revenue && revenue.revenue_over_time.length > 0 && (
        <div className="card chart-wrap" style={{ marginBottom: 22 }}>
          <h4>Revenue over time</h4>
          <div className="sub">By month</div>
          <div className="chart">
            {revenue.revenue_over_time.map((m) => (
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
        </div>
      )}

      {recent.length > 0 && (
        <div className="card">
          <h4>Recent activity</h4>
          <div className="sub">Latest audit events</div>
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Entity</th>
                  <th>Actor</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((row) => (
                  <tr key={row.id}>
                    <td className="num">{row.action}</td>
                    <td>
                      {row.entity_type}
                      {row.entity_id ? ` #${row.entity_id}` : ""}
                    </td>
                    <td className="num">{row.actor_id ?? "—"}</td>
                    <td className="num">{new Date(row.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
