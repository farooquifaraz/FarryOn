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
}

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
      const res = await api<Envelope<UserRow[]>>(`/api/v1/users?${params}`);
      setRows(res.data);
      setTotal(res.meta?.total ?? 0);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Failed to load users.");
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter, kind]);

  useEffect(() => {
    void load();
  }, [load]);

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
        <Can permission="users.create">
          <button className="btn-primary" onClick={() => setInviteOpen(true)}>
            Invite user
          </button>
        </Can>
      </div>

      <div className="toolbar" style={{ marginBottom: 10 }}>
        <button className={`chip${kind === "app" ? " on" : ""}`} onClick={() => { setKind("app"); setPage(1); }}>App users</button>
        <button className={`chip${kind === "staff" ? " on" : ""}`} onClick={() => { setKind("staff"); setPage(1); }}>Staff</button>
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

      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Country</th>
              <th>{kind === "staff" ? "Roles" : "Plan role"}</th>
              <th>Status</th>
              <th>Verified</th>
              <th>Joined</th>
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
                  <td>{row.roles.join(", ") || "—"}</td>
                  <td><span className={`pill ${STATUS_PILL[row.status] ?? "muted"}`}>{row.status}</span></td>
                  <td>{row.email_verified ? "✓" : "—"}</td>
                  <td className="num">{new Date(row.created_at).toLocaleDateString()}</td>
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
    </>
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
