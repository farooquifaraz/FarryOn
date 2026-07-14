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
  created_at: string;
}
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
  const { user: me, can, startImpersonation } = useAuth();
  const [rows, setRows] = useState<UserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<(typeof FILTERS)[number]>("all");
  const [roles, setRoles] = useState<Role[]>([]);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [rolesFor, setRolesFor] = useState<UserRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (search) params.set("search", search);
      if (statusFilter !== "all") params.set("status", statusFilter);
      const res = await api<Envelope<UserRow[]>>(`/api/v1/users?${params}`);
      setRows(res.data);
      setTotal(res.meta?.total ?? 0);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Failed to load users.");
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter]);

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

  async function impersonate(row: UserRow) {
    setError(null);
    try {
      await startImpersonation(row.id);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Impersonation failed.");
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Users</h2>
          <p>{total} accounts</p>
        </div>
        <Can permission="users.create">
          <button className="btn-primary" onClick={() => setInviteOpen(true)}>
            Invite user
          </button>
        </Can>
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
              <th>Roles</th>
              <th>Status</th>
              <th>Verified</th>
              <th>Joined</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="loading">Loading…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={6} className="empty">No users match.</td></tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <b>{row.display_name ?? "—"}</b>
                    <div style={{ color: "var(--td)", fontSize: 11 }}>{row.email}</div>
                  </td>
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
                        <Can permission="users.impersonate">
                          <button className="btn-outline btn-sm" onClick={() => impersonate(row)}>Login as</button>
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
