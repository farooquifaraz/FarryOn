import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiRequestError, type Envelope } from "../lib/api";
import { Can } from "../lib/auth";

interface Role {
  id: number;
  name: string;
  description: string | null;
  level: number;
  is_system: boolean;
  permissions: string[];
}
interface Permission {
  code: string;
  description: string | null;
}

export default function Roles() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, p] = await Promise.all([
        api<Envelope<Role[]>>("/api/v1/roles"),
        api<Envelope<Permission[]>>("/api/v1/permissions"),
      ]);
      setRoles(r.data);
      setPermissions(p.data);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Failed to load roles.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove(role: Role) {
    if (!window.confirm(`Delete role "${role.name}"?`)) return;
    try {
      await api(`/api/v1/roles/${role.id}`, { method: "DELETE" });
      void load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Delete failed.");
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Roles</h2>
          <p>Permission bundles — higher level outranks lower</p>
        </div>
        <Can permission="roles.manage">
          <button className="btn-primary" onClick={() => setEditing("new")}>
            New role
          </button>
        </Can>
      </div>

      {error && <div className="error-text" style={{ textAlign: "left", marginBottom: 10 }}>{error}</div>}

      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>Role</th>
              <th>Level</th>
              <th>Permissions</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <tr key={role.id}>
                <td>
                  <b>{role.name}</b>
                  {role.is_system && <span className="pill muted" style={{ marginLeft: 8 }}>system</span>}
                  <div style={{ color: "var(--td)", fontSize: 11 }}>{role.description}</div>
                </td>
                <td className="num">{role.level}</td>
                <td style={{ maxWidth: 380 }}>
                  <span style={{ color: "var(--tm)", fontSize: 11.5, fontFamily: "var(--fm)" }}>
                    {role.permissions.length === permissions.length && role.is_system
                      ? "all"
                      : role.permissions.join(", ") || "—"}
                  </span>
                </td>
                <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                  {!role.is_system && (
                    <Can permission="roles.manage">
                      <span style={{ display: "inline-flex", gap: 6 }}>
                        <button className="btn-outline btn-sm" onClick={() => setEditing(role)}>Edit</button>
                        <button className="btn-outline btn-sm danger" onClick={() => remove(role)}>Delete</button>
                      </span>
                    </Can>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <RoleModal
          role={editing === "new" ? null : editing}
          permissions={permissions}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            void load();
          }}
        />
      )}
    </>
  );
}

function RoleModal({
  role,
  permissions,
  onClose,
  onDone,
}: {
  role: Role | null;
  permissions: Permission[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [level, setLevel] = useState(role?.level ?? 10);
  const [codes, setCodes] = useState<string[]>(role?.permissions ?? []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (role) {
        await api(`/api/v1/roles/${role.id}`, {
          method: "PATCH",
          body: { description, level, permission_codes: codes },
        });
      } else {
        await api("/api/v1/roles", {
          method: "POST",
          body: { name, description, level, permission_codes: codes },
        });
      }
      onDone();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Save failed.");
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h3>{role ? `Edit role — ${role.name}` : "New role"}</h3>
        {!role && (
          <div className="field">
            <label>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} style={{ width: "100%" }} />
          </div>
        )}
        <div className="field">
          <label>Description</label>
          <input value={description} onChange={(e) => setDescription(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div className="field">
          <label>Level (0–99)</label>
          <input
            type="number"
            min={0}
            max={99}
            value={level}
            onChange={(e) => setLevel(Number(e.target.value))}
            style={{ width: 120 }}
          />
        </div>
        <label>Permissions</label>
        {permissions.map((perm) => (
          <div className="checkbox-row" key={perm.code}>
            <input
              type="checkbox"
              id={`perm-${perm.code}`}
              checked={codes.includes(perm.code)}
              onChange={(e) =>
                setCodes((prev) => (e.target.checked ? [...prev, perm.code] : prev.filter((c) => c !== perm.code)))
              }
            />
            <label htmlFor={`perm-${perm.code}`} style={{ margin: 0, textTransform: "none", fontSize: 12, color: "var(--t)" }}>
              <span className="num">{perm.code}</span>
              <span style={{ color: "var(--td)" }}> — {perm.description}</span>
            </label>
          </div>
        ))}
        {error && <div className="error-text">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn-outline" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </div>
  );
}
