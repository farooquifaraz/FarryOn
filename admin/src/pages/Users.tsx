import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiRequestError, type Envelope } from "../lib/api";
import { Can, useAuth } from "../lib/auth";
import Pager from "../components/Pager";

interface UserRow {
  id: number;
  email: string | null;
  display_name: string | null;
  status: string;
  email_verified: boolean;
  roles: string[];
  is_staff: boolean;
  country: string | null;
  timezone: string | null;
  created_at: string;
  // billing/nudge.py — the talk budget, the app build, the upgrade nudge
  quota?: { plan: string; used_s: number; cap_s: number; window: string; state: "ok" | "near" | "out" | "unlimited" };
  app?: { build: number | null; platform: string | null; seen_at: string | null; latest_build: number | null; behind: number | null; outdated: boolean };
  nudge?: { last_at: string | null; opt_out: boolean; can_send: boolean; next_at: string | null };
}

interface UsersSummary {
  app_users: number;
  paying: number;
  out_of_quota: number;
  near_limit: number;
  outdated_app: number;
  latest_build: number | null;
}

/** "out" = talk time used up, "near" = 80 %+ of it, "outdated" = an older
 * app build than the website serves. */
const ATTENTION = ["all", "out", "near", "outdated"] as const;
type Attention = (typeof ATTENTION)[number];
const ATTENTION_LABEL: Record<Attention, string> = {
  all: "All",
  out: "Out of quota",
  near: "Near limit",
  outdated: "Outdated app",
};

const ago = (iso: string | null) => {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const h = Math.round(ms / 3_600_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "yesterday" : `${d} d ago`;
};
const inDays = (iso: string) => Math.max(1, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));

const COUNTRY_NAMES: Record<string, string> = {
  AE: "UAE", IN: "India", SA: "Saudi Arabia", QA: "Qatar", OM: "Oman", BH: "Bahrain", KW: "Kuwait",
  PK: "Pakistan", BD: "Bangladesh", GB: "UK", US: "USA", CA: "Canada", AU: "Australia", DE: "Germany",
  FR: "France", EG: "Egypt", SG: "Singapore", MY: "Malaysia", TR: "Türkiye",
};
const flag = (cc: string) => cc.toUpperCase().replace(/./g, (ch) => String.fromCodePoint(127397 + ch.charCodeAt(0)));
interface Role {
  id: number;
  name: string;
  level: number;
  is_system: boolean;
}

const STATUS_PILL: Record<string, string> = {
  active: "good",
  invited: "warn",
  suspended: "crit",
  deactivated: "muted",
};
const FILTERS = ["all", "active", "invited", "suspended", "deactivated"] as const;
const PAGE_SIZE = 20;

export default function Users() {
  const { user: me, can } = useAuth();
  const [rows, setRows] = useState<UserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<(typeof FILTERS)[number]>("all");
  // App users (signed up in the app, role "user") and Staff (any admin role)
  // are two lists; nobody has to read roles to tell them apart.
  const [kind, setKind] = useState<"app" | "staff">("app");
  const [attention, setAttention] = useState<Attention>(() => {
    const a = new URLSearchParams(window.location.search).get("attention");
    return (ATTENTION as readonly string[]).includes(a ?? "") ? (a as Attention) : "all";
  });
  const [summary, setSummary] = useState<UsersSummary | null>(null);
  const [nudgeAll, setNudgeAll] = useState(false);
  const [nudgeFor, setNudgeFor] = useState<UserRow | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [rolesFor, setRolesFor] = useState<UserRow | null>(null);
  const [linkFor, setLinkFor] = useState<UserRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (search) params.set("search", search);
      if (statusFilter !== "all") params.set("status", statusFilter);
      params.set("kind", kind);
      if (kind === "app" && (attention === "out" || attention === "near")) params.set("quota", attention);
      if (kind === "app" && attention === "outdated") params.set("app", "outdated");
      const res = await api<Envelope<UserRow[]>>(`/api/v1/users?${params}`);
      setRows(res.data);
      setTotal(res.meta?.total ?? 0);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Failed to load users.");
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter, kind, attention]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadSummary = useCallback(() => {
    api<Envelope<UsersSummary>>("/api/v1/users/summary").then((r) => setSummary(r.data)).catch(() => {});
  }, []);
  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    if (can("permissions.read"))
      api<Envelope<Role[]>>("/api/v1/roles").then((r) => setRoles(r.data)).catch(() => {});
  }, [can]);

  async function act(row: UserRow, action: "suspend" | "activate" | "delete") {
    setError(null);
    try {
      if (action === "delete") {
        if (!window.confirm(`Delete ${row.email}? The account is soft-deleted and its email freed for reuse.`)) return;
        await api(`/api/v1/users/${row.id}`, { method: "DELETE" });
      } else {
        await api(`/api/v1/users/${row.id}`, {
          method: "PATCH",
          body: { status: action === "suspend" ? "suspended" : "active" },
        });
      }
      void load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Action failed.");
    }
  }


  return (
    <>
      <div className="page-head">
        <div>
          <h2>Users</h2>
          <p>{total} {kind === "app" ? "app users" : "staff accounts"}</p>
        </div>
        <span style={{ display: "inline-flex", gap: 8 }}>
          {kind === "app" && summary && summary.out_of_quota > 0 && (
            <Can permission="billing.manage">
              <button className="btn-outline" onClick={() => setNudgeAll(true)}>
                Email all out-of-quota ({summary.out_of_quota})
              </button>
            </Can>
          )}
          <Can permission="users.create">
            <button className="btn-primary" onClick={() => setInviteOpen(true)}>
              Invite user
            </button>
          </Can>
        </span>
      </div>

      <div className="toolbar" style={{ marginBottom: 10 }}>
        <button className={`chip${kind === "app" ? " on" : ""}`} onClick={() => { setKind("app"); setPage(1); }}>App users</button>
        <button className={`chip${kind === "staff" ? " on" : ""}`} onClick={() => { setKind("staff"); setPage(1); }}>Staff</button>
        {kind === "app" && (
          <span style={{ display: "inline-flex", gap: 6, marginLeft: 14 }}>
            {ATTENTION.map((a) => {
              const n = a === "out" ? summary?.out_of_quota : a === "near" ? summary?.near_limit : a === "outdated" ? summary?.outdated_app : null;
              return (
                <button
                  key={a}
                  className={`chip attention-${a}${attention === a ? " on" : ""}`}
                  onClick={() => { setAttention(a); setPage(1); }}
                >
                  {ATTENTION_LABEL[a]}{n != null ? ` · ${n}` : ""}
                </button>
              );
            })}
          </span>
        )}
      </div>
      <div className="toolbar">
        <input
          type="search"
          placeholder="Search by name or email"
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
            {f}
          </button>
        ))}
        <Can permission="users.read">
          <a className="btn-outline btn-sm" href="/api/v1/users/export" onClick={(e) => { e.preventDefault(); void exportCsv(); }}>
            Export CSV
          </a>
        </Can>
      </div>

      {error && <div className="error-text" style={{ textAlign: "left", marginBottom: 10 }}>{error}</div>}
      {notice && <div className="notice-text" style={{ marginBottom: 10 }}>{notice}</div>}

      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Country</th>
              <th>{kind === "staff" ? "Roles" : "Plan · talk time"}</th>
              <th>Status</th>
              {kind === "app" ? <th title={summary?.latest_build ? `Latest on the website: ${summary.latest_build}` : undefined}>App{summary?.latest_build ? ` · latest ${summary.latest_build}` : ""}</th> : <th>Verified</th>}
              {kind === "app" ? <th>Last nudge</th> : <th>Joined</th>}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="loading">Loading…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={7} className="empty">No users match.</td></tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <b>{row.display_name ?? "—"}</b>
                    <div style={{ color: "var(--td)", fontSize: 11 }}>{row.email}</div>
                  </td>
                  <td title={row.timezone ?? ""}>{row.country ? `${flag(row.country)} ${COUNTRY_NAMES[row.country] ?? row.country}` : <span style={{ color: "var(--td)" }}>—</span>}</td>
                  <td>{kind === "staff" ? (row.roles.join(", ") || "—") : <TalkCell row={row} />}</td>
                  <td>
                    <span className={`pill ${STATUS_PILL[row.status] ?? "muted"}`}>{row.status}</span>
                    {kind === "app" && <QuotaBadge row={row} />}
                  </td>
                  {kind === "app" ? <td><AppCell row={row} /></td> : <td>{row.email_verified ? "✓" : "—"}</td>}
                  {kind === "app" ? <td><NudgeCell row={row} /></td> : <td className="num">{new Date(row.created_at).toLocaleDateString()}</td>}
                  <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                    {row.id !== me?.id && (
                      <span style={{ display: "inline-flex", gap: 6 }}>
                        <Can permission="users.update">
                          <button className="btn-outline btn-sm" onClick={() => setRolesFor(row)}>Roles</button>
                          {row.status === "suspended" ? (
                            <button className="btn-outline btn-sm" onClick={() => act(row, "activate")}>Activate</button>
                          ) : (
                            <button className="btn-outline btn-sm" onClick={() => act(row, "suspend")}>Suspend</button>
                          )}
                        </Can>
                        <Can permission="billing.manage">
                          {kind === "app" && row.nudge && (
                            <button
                              className="btn-outline btn-sm"
                              disabled={!row.nudge.can_send}
                              title={
                                row.nudge.opt_out ? "They asked for no more offers"
                                  : !row.email_verified ? "No verified email"
                                  : row.nudge.next_at ? `Sent recently — again in ${inDays(row.nudge.next_at)} d`
                                  : "Send the upgrade email now"
                              }
                              onClick={() => setNudgeFor(row)}
                            >
                              Upgrade email
                            </button>
                          )}
                          <button className="btn-outline btn-sm" onClick={() => setLinkFor(row)}>Payment link</button>
                        </Can>
                        <Can permission="users.delete">
                          <button className="btn-outline btn-sm danger" onClick={() => act(row, "delete")}>Delete</button>
                        </Can>
                      </span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />

      {inviteOpen && (
        <InviteModal roles={roles} onClose={() => setInviteOpen(false)} onDone={() => { setInviteOpen(false); void load(); }} />
      )}
      {rolesFor && (
        <RolesModal user={rolesFor} roles={roles} onClose={() => setRolesFor(null)} onDone={() => { setRolesFor(null); void load(); }} />
      )}
      {linkFor && <PaymentLinkModal user={linkFor} onClose={() => setLinkFor(null)} />}
      {nudgeFor && (
        <NudgeOneModal
          user={nudgeFor}
          onClose={() => setNudgeFor(null)}
          onSent={() => { void load(); loadSummary(); }}
        />
      )}
      {nudgeAll && summary && (
        <NudgeAllModal
          count={summary.out_of_quota}
          onClose={() => setNudgeAll(false)}
          onSent={(msg) => { setNotice(msg); void load(); loadSummary(); }}
        />
      )}
    </>
  );
}

const NUDGE_REASON: Record<string, string> = {
  recent: "emailed in the last 7 days",
  opted_out: "they asked for no more offers",
  no_verified_email: "no verified email address",
};

/** "35 / 30 min" with a bar in the plan's colour: red past the cap, amber
 * from 80 %, teal below. */
function TalkCell({ row }: { row: UserRow }) {
  const q = row.quota;
  if (!q) return <span style={{ color: "var(--td)" }}>—</span>;
  const used = Math.round(q.used_s / 60);
  const cap = q.cap_s < 0 ? null : Math.round(q.cap_s / 60);
  const share = cap ? Math.min(1, q.used_s / Math.max(q.cap_s, 1)) : 0;
  const color = q.state === "out" ? "var(--crit)" : q.state === "near" ? "var(--gold)" : "var(--pl)";
  return (
    <div style={{ minWidth: 120 }}>
      <div style={{ fontSize: 12 }}>
        <b>{q.plan}</b>
        <span style={{ color: "var(--td)" }}> · {q.window === "lifetime" ? "trial" : q.window}</span>
      </div>
      <div className="num" style={{ fontSize: 12 }}>{cap == null ? `${used} min / ∞` : `${used} / ${cap} min`}</div>
      {cap != null && (
        <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.08)", marginTop: 4 }}>
          <div style={{ width: `${share * 100}%`, height: 4, borderRadius: 2, background: color }} />
        </div>
      )}
    </div>
  );
}

function QuotaBadge({ row }: { row: UserRow }) {
  const st = row.quota?.state;
  if (st === "out") return <span className="pill crit" style={{ marginLeft: 6 }}>Talk time used up</span>;
  if (st === "near") return <span className="pill warn" style={{ marginLeft: 6 }}>Near the limit</span>;
  return null;
}

function AppCell({ row }: { row: UserRow }) {
  const a = row.app;
  if (!a || a.build == null) return <span style={{ color: "var(--td)" }}>never connected</span>;
  const behind = a.behind ?? 0;
  const color = behind === 0 ? "var(--pl)" : behind < 10 ? "var(--gold)" : "var(--crit)";
  return (
    <div>
      <div className="num" style={{ fontSize: 12, color }}>
        {a.build} · {a.latest_build == null ? "—" : behind === 0 ? "latest" : `${behind} behind`}
      </div>
      <div style={{ fontSize: 11, color: "var(--td)" }}>{a.platform ?? "—"} · seen {ago(a.seen_at)}</div>
    </div>
  );
}

function NudgeCell({ row }: { row: UserRow }) {
  const n = row.nudge;
  if (!n) return <span style={{ color: "var(--td)" }}>—</span>;
  if (n.opt_out) return <span style={{ color: "var(--td)" }}>opted out</span>;
  if (!n.last_at) return <span style={{ color: "var(--td)" }}>never</span>;
  return (
    <div>
      <div style={{ fontSize: 12 }}>{new Date(n.last_at).toLocaleDateString()}</div>
      {n.next_at && <div style={{ fontSize: 11, color: "var(--td)" }}>again in {inDays(n.next_at)} d</div>}
    </div>
  );
}

/** Ask first, then say what happened — in the same box, so the admin sees
 *  "sent" (or why not) before the table refreshes under them. */
function NudgeOneModal({ user, onClose, onSent }: { user: UserRow; onClose: () => void; onSent: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const q = user.quota;
  const used = q ? Math.round(q.used_s / 60) : null;
  const cap = q && q.cap_s >= 0 ? Math.round(q.cap_s / 60) : null;

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<Envelope<{ result: string; reason: string | null }>>(`/api/v1/users/${user.id}/upgrade-email`, { method: "POST" });
      const ok = r.data.result === "sent";
      setResult({
        ok,
        text: ok
          ? `The upgrade email is on its way to ${user.email}.`
          : `Not sent: ${NUDGE_REASON[r.data.reason ?? ""] ?? r.data.reason}.`,
      });
      if (ok) onSent();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not send the email.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {result ? (
          <>
            <h3>{result.ok ? "Email sent" : "Email not sent"}</h3>
            <p className={result.ok ? "notice-text" : "error-text"} style={{ textAlign: "left", fontSize: 13, lineHeight: 1.5 }}>{result.text}</p>
            <div className="modal-actions">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </>
        ) : (
          <>
            <h3>Send the upgrade email to {user.display_name ?? user.email}?</h3>
            <p style={{ color: "var(--tm)", fontSize: 13, lineHeight: 1.5 }}>
              To <b>{user.email}</b>{q ? <> — {used} min used{cap != null ? ` of ${cap}` : ""} on the {q.plan} plan</> : null}.
              The email shows their own numbers and one button to the plans on the website.
            </p>
            {error && <div className="error-text">{error}</div>}
            <div className="modal-actions">
              <button className="btn-outline" onClick={onClose} disabled={busy}>Cancel</button>
              <button className="btn-primary" onClick={send} disabled={busy}>{busy ? "Sending…" : "Send email"}</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function NudgeAllModal({ count, onClose, onSent }: { count: number; onClose: () => void; onSent: (msg: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<Envelope<{ sent: number[]; skipped: { id: number; reason: string }[] }>>("/api/v1/users/upgrade-email", {
        method: "POST",
        body: { all_out_of_quota: true },
      });
      const skipped = r.data.skipped.length;
      const msg = `Upgrade email sent to ${r.data.sent.length} user${r.data.sent.length === 1 ? "" : "s"}${skipped ? `, ${skipped} skipped (${r.data.skipped.map((x) => NUDGE_REASON[x.reason] ?? x.reason).filter((v, i, a) => a.indexOf(v) === i).join("; ")})` : ""}.`;
      setResult(msg);
      onSent(msg);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Sending failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {result ? (
          <>
            <h3>Emails sent</h3>
            <p className="notice-text" style={{ fontSize: 13, lineHeight: 1.5 }}>{result}</p>
            <div className="modal-actions">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </>
        ) : (
          <>
            <h3>Send the upgrade email to {count} user{count === 1 ? "" : "s"}?</h3>
            <p style={{ color: "var(--tm)", fontSize: 13, lineHeight: 1.5 }}>
              Everyone who has used up their talk time and has not upgraded. Each email shows their own numbers and one button to the plans on the website.
              Anyone emailed in the last 7 days, without a verified address, or who opted out is skipped.
            </p>
            {error && <div className="error-text">{error}</div>}
            <div className="modal-actions">
              <button className="btn-outline" onClick={onClose} disabled={busy}>Cancel</button>
              <button className="btn-primary" onClick={send} disabled={busy}>{busy ? "Sending…" : `Send ${count} email${count === 1 ? "" : "s"}`}</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

async function exportCsv() {
  const text = await api<string>("/api/v1/users/export");
  const blob = new Blob([text], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "users.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function InviteModal({ roles, onClose, onDone }: { roles: Role[]; onClose: () => void; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [roleIds, setRoleIds] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/users", {
        method: "POST",
        body: { email, display_name: displayName || null, role_ids: roleIds },
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Invite failed.");
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h3>Invite user</h3>
        <div className="field">
          <label>Email</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Display name (optional)</label>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} style={{ width: "100%" }} />
        </div>
        <label>Roles</label>
        {roles.filter((r) => !r.is_system).map((role) => (
          <div className="checkbox-row" key={role.id}>
            <input
              type="checkbox"
              id={`invite-role-${role.id}`}
              checked={roleIds.includes(role.id)}
              onChange={(e) =>
                setRoleIds((prev) => (e.target.checked ? [...prev, role.id] : prev.filter((id) => id !== role.id)))
              }
            />
            <label htmlFor={`invite-role-${role.id}`} style={{ margin: 0, textTransform: "none", fontSize: 12.5, color: "var(--t)" }}>
              {role.name}
            </label>
          </div>
        ))}
        {error && <div className="error-text">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn-outline" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? "Inviting…" : "Send invite"}</button>
        </div>
      </form>
    </div>
  );
}

function RolesModal({ user, roles, onClose, onDone }: { user: UserRow; roles: Role[]; onClose: () => void; onDone: () => void }) {
  const [roleIds, setRoleIds] = useState<number[]>(
    roles.filter((r) => user.roles.includes(r.name)).map((r) => r.id),
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/users/${user.id}/roles`, { method: "PUT", body: { role_ids: roleIds } });
      onDone();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Update failed.");
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h3>Roles — {user.email}</h3>
        {roles.map((role) => (
          <div className="checkbox-row" key={role.id}>
            <input
              type="checkbox"
              id={`user-role-${role.id}`}
              checked={roleIds.includes(role.id)}
              onChange={(e) =>
                setRoleIds((prev) => (e.target.checked ? [...prev, role.id] : prev.filter((id) => id !== role.id)))
              }
            />
            <label htmlFor={`user-role-${role.id}`} style={{ margin: 0, textTransform: "none", fontSize: 12.5, color: "var(--t)" }}>
              {role.name} <span style={{ color: "var(--td)" }}>· level {role.level}</span>
            </label>
          </div>
        ))}
        {error && <div className="error-text">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn-outline" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save roles"}</button>
        </div>
      </form>
    </div>
  );
}

interface PlanRow {
  id: number;
  name: string;
  title: string;
  price_cents: number;
  currency: string;
  interval: "month" | "year";
  one_time: boolean;
  region: string | null;
  is_active: boolean;
}
interface PaymentLink {
  url: string;
  plan: string;
  plan_title: string;
  price_cents: number;
  currency: string;
  interval: "month" | "year";
  one_time: boolean;
  user_email: string | null;
  valid_hours: number;
}

/** "₹599", "AED 25", "$15.00" — whole rupees/dirhams, cents for dollars. */
function money(cents: number, currency: string): string {
  const c = currency.toUpperCase();
  if (c === "INR") return `₹${Math.round(cents / 100).toLocaleString("en-IN")}`;
  if (c === "AED") return cents % 100 === 0 ? `AED ${cents / 100}` : `AED ${(cents / 100).toFixed(2)}`;
  if (c === "USD") return `$${(cents / 100).toFixed(2)}`;
  return `${c} ${(cents / 100).toFixed(2)}`;
}

function planLabel(p: PlanRow): string {
  const period = p.one_time ? (p.interval === "year" ? "for 12 months" : "for 30 days") : p.interval === "year" ? "/yr" : "/mo";
  const where = p.region ? ` · ${p.region}` : "";
  return `${p.title} — ${money(p.price_cents, p.currency)} ${period}${where}`;
}

/**
 * Mint a Stripe Checkout link for this user and one plan, to send by hand
 * (WhatsApp, email). It is the app's own checkout, so paying it activates
 * the plan through the same webhook; nothing else to do afterwards.
 */
function PaymentLinkModal({ user, onClose }: { user: UserRow; onClose: () => void }) {
  const [plans, setPlans] = useState<PlanRow[]>([]);
  const [plan, setPlan] = useState("");
  const [link, setLink] = useState<PaymentLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api<Envelope<PlanRow[]>>("/api/v1/admin/plans")
      .then((r) => {
        const sold = r.data.filter((p) => p.is_active && p.price_cents > 0);
        setPlans(sold);
        if (sold.length && !plan) setPlan(sold[0].name);
      })
      .catch((err) => setError(err instanceof ApiRequestError ? err.message : "Could not load plans."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<Envelope<PaymentLink>>("/api/v1/admin/payment-links", {
        method: "POST",
        body: { user_id: user.id, plan },
      });
      setLink(r.data);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not create the link.");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Copy failed — select the link and copy it by hand.");
    }
  }

  const message = link
    ? `Hi${user.display_name ? " " + user.display_name : ""}, here is your FarryOn ${link.plan_title} plan (${money(link.price_cents, link.currency)}${link.one_time ? (link.interval === "year" ? " for 12 months" : " for 30 days") : link.interval === "year" ? " a year" : " a month"}). Pay securely here (link valid ${link.valid_hours} hours): ${link.url}`
    : "";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h3>Payment link — {user.email}</h3>
        {!link ? (
          <>
            <div className="field">
              <label>Plan</label>
              <select value={plan} onChange={(e) => setPlan(e.target.value)} style={{ width: "100%" }} required>
                {plans.map((p) => (
                  <option key={p.name} value={p.name}>{planLabel(p)}</option>
                ))}
              </select>
            </div>
            <p style={{ color: "var(--td)", fontSize: 12 }}>
              The same Stripe checkout the app opens. When they pay, the plan switches on by itself. A user already on a paid plan cannot be sent a second one.
            </p>
            {error && <div className="error-text">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="btn-outline" onClick={onClose}>Cancel</button>
              <button className="btn-primary" disabled={busy || !plan}>{busy ? "Creating…" : "Create link"}</button>
            </div>
          </>
        ) : (
          <>
            <div className="field">
              <label>{link.plan_title} · {money(link.price_cents, link.currency)} · valid {link.valid_hours} h</label>
              <input readOnly value={link.url} onFocus={(e) => e.currentTarget.select()} style={{ width: "100%" }} />
            </div>
            {error && <div className="error-text">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="btn-outline" onClick={onClose}>Close</button>
              <a className="btn-outline" href={`https://wa.me/?text=${encodeURIComponent(message)}`} target="_blank" rel="noreferrer">WhatsApp</a>
              {user.email && (
                <a className="btn-outline" href={`mailto:${user.email}?subject=${encodeURIComponent("Your FarryOn plan")}&body=${encodeURIComponent(message)}`}>Email</a>
              )}
              <button type="button" className="btn-primary" onClick={() => void copy()}>{copied ? "Copied ✓" : "Copy link"}</button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
