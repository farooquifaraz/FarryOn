import { useCallback, useEffect, useState } from "react";
import { api, ApiRequestError, type Envelope } from "../lib/api";
import Pager from "../components/Pager";

interface AuditRow {
  id: number;
  actor_id: number | null;
  impersonator_id: number | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before: unknown;
  after: unknown;
  ip: string | null;
  created_at: string;
}

const PAGE_SIZE = 25;

export default function Audit() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (action) params.set("action", action);
      const res = await api<Envelope<AuditRow[]>>(`/api/v1/audit-logs?${params}`);
      setRows(res.data);
      setTotal(res.meta?.total ?? 0);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Failed to load audit log.");
    }
  }, [page, action]);

  useEffect(() => {
    void load();
  }, [load]);

  async function exportCsv() {
    const text = await api<string>("/api/v1/audit-logs/export");
    const blob = new Blob([text], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "audit_logs.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Audit log</h2>
          <p>Append-only — every auth event and admin mutation</p>
        </div>
        <button className="btn-outline" onClick={() => void exportCsv()}>
          Export CSV
        </button>
      </div>

      <div className="toolbar">
        <input
          type="search"
          placeholder="Filter by exact action, e.g. auth.login"
          value={action}
          onChange={(e) => {
            setAction(e.target.value.trim());
            setPage(1);
          }}
        />
      </div>

      {error && <div className="error-text" style={{ textAlign: "left", marginBottom: 10 }}>{error}</div>}

      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Action</th>
              <th>Entity</th>
              <th>Actor</th>
              <th>Impersonator</th>
              <th>IP</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={7} className="empty">No events match.</td></tr>
            ) : (
              rows.map((row) => (
                <>
                  <tr key={row.id}>
                    <td className="num">{new Date(row.created_at).toLocaleString()}</td>
                    <td className="num">{row.action}</td>
                    <td>
                      {row.entity_type}
                      {row.entity_id ? ` #${row.entity_id}` : ""}
                    </td>
                    <td className="num">{row.actor_id ?? "—"}</td>
                    <td className="num">{row.impersonator_id ?? "—"}</td>
                    <td className="num">{row.ip ?? "—"}</td>
                    <td>
                      {Boolean(row.before || row.after) && (
                        <button
                          className="btn-outline btn-sm"
                          onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                        >
                          {expanded === row.id ? "Hide" : "Diff"}
                        </button>
                      )}
                    </td>
                  </tr>
                  {expanded === row.id && (
                    <tr key={`${row.id}-diff`}>
                      <td colSpan={7} style={{ background: "rgba(0,212,170,0.02)" }}>
                        <pre style={{ fontSize: 11, fontFamily: "var(--fm)", whiteSpace: "pre-wrap", color: "var(--tm)" }}>
                          {JSON.stringify({ before: row.before, after: row.after }, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </>
              ))
            )}
          </tbody>
        </table>
      </div>
      <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
    </>
  );
}
