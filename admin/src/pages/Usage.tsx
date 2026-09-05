import { useCallback, useEffect, useState } from "react";
import { api, ApiRequestError, type Envelope } from "../lib/api";
import Pager from "../components/Pager";

/** One metered feature for one user, over the window the row names. */
interface Metric {
  used: number;
  cap: number; // -1 = unlimited, 0 = not included
  unit: "seconds" | "count";
}
interface UsageRow {
  user_id: number;
  email: string | null;
  display_name: string | null;
  status: string;
  plan: string;
  /** "YYYY-MM", or "lifetime" for a trial user — whose caps are lifetime. */
  window: string;
  metrics: Record<string, Metric>;
}
interface UsagePayload {
  items: UsageRow[];
  total: number;
  page: number;
  page_size: number;
  month: string;
}

const PAGE_SIZE = 20;

// Order and labels for the columns. `translate_seconds` is shown apart because
// it is a BREAKDOWN of talk time, not a budget of its own — the two share one
// allowance, so showing it with a cap would read as a second one.
const COLUMNS: { key: string; label: string; capped: boolean }[] = [
  { key: "voice_seconds", label: "Talk time", capped: true },
  { key: "translate_seconds", label: "…of which translation", capped: false },
  { key: "image_scans", label: "Image scans", capped: true },
  { key: "web_searches", label: "Web searches", capped: true },
  { key: "text_turns", label: "Typed messages", capped: false },
  { key: "frames_sent", label: "Camera frames", capped: false },
];

const fmt = (m: Metric | undefined, capped: boolean) => {
  if (!m) return "—";
  const used = m.unit === "seconds" ? `${Math.round(m.used / 60)} min` : `${m.used}`;
  if (!capped) return used;
  if (m.cap < 0) return `${used} / ∞`;
  if (m.cap === 0) return "not included";
  const cap = m.unit === "seconds" ? `${Math.round(m.cap / 60)} min` : `${m.cap}`;
  return `${used} / ${cap}`;
};

/** 0–1 share of the cap, for the bar. Uncapped metrics have no bar. */
const share = (m: Metric | undefined) =>
  !m || m.cap <= 0 ? null : Math.min(1, m.used / m.cap);

function lastMonths(count: number): string[] {
  const out: string[] = [];
  const d = new Date();
  for (let i = 0; i < count; i++) {
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
    d.setMonth(d.getMonth() - 1);
  }
  return out;
}

export default function Usage() {
  const [rows, setRows] = useState<UsageRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [month, setMonth] = useState(lastMonths(1)[0]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(PAGE_SIZE),
        month,
      });
      if (search) params.set("q", search);
      const res = await api<Envelope<UsagePayload>>(`/api/v1/users/usage?${params}`);
      setRows(res.data.items);
      setTotal(res.data.total);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Failed to load usage.");
    }
  }, [page, month, search]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Usage</h2>
          <p>What each person actually used — and how close they are to their plan</p>
        </div>
      </div>

      {error && (
        <div className="error-text" style={{ textAlign: "left", marginBottom: 10 }}>{error}</div>
      )}

      <div className="filters">
        <input
          className="input"
          placeholder="Search name or email…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        {lastMonths(6).map((m) => (
          <button
            key={m}
            className={`chip${month === m ? " on" : ""}`}
            onClick={() => {
              setMonth(m);
              setPage(1);
            }}
          >
            {m}
          </button>
        ))}
      </div>

      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Plan</th>
              {COLUMNS.map((c) => (
                <th key={c.key}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={COLUMNS.length + 2} className="empty">
                  Nobody matches.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.user_id}>
                  <td>
                    <b>{row.display_name ?? "—"}</b>
                    <div style={{ color: "var(--td)", fontSize: 11 }}>{row.email}</div>
                  </td>
                  <td>
                    {row.plan}
                    {row.window === "lifetime" && (
                      // A trial's allowance is a one-time total, so its numbers
                      // are not "this month" — say so rather than mislead.
                      <div style={{ color: "var(--td)", fontSize: 11 }}>lifetime</div>
                    )}
                  </td>
                  {COLUMNS.map((c) => {
                    const m = row.metrics[c.key];
                    const pct = c.capped ? share(m) : null;
                    return (
                      <td key={c.key} className="num">
                        {fmt(m, c.capped)}
                        {pct !== null && (
                          <div
                            style={{
                              height: 3,
                              marginTop: 4,
                              borderRadius: 2,
                              background: "rgba(255,255,255,.08)",
                            }}
                          >
                            <div
                              style={{
                                width: `${Math.round(pct * 100)}%`,
                                height: "100%",
                                borderRadius: 2,
                                background: pct >= 0.9 ? "var(--crit, #e5484d)" : "var(--pl, #18b98a)",
                              }}
                            />
                          </div>
                        )}
                      </td>
                    );
                  })}
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
