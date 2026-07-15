import { NavLink, Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/users", label: "Users" },
  { to: "/roles", label: "Roles" },
  { to: "/billing", label: "Subscriptions & Revenue" },
  { to: "/audit", label: "Audit log" },
];

export default function Layout() {
  const { user, loading, logout, impersonating, stopImpersonation } = useAuth();

  if (loading) return <div className="loading">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;

  return (
    <>
      {impersonating && (
        <div className="impersonation-banner">
          Viewing as {impersonating.email ?? `user #${impersonating.id}`} — actions are audit-logged with both identities.
          <button className="btn-outline btn-sm" onClick={stopImpersonation}>
            Return to admin
          </button>
        </div>
      )}
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="mark" />
            <span>
              Farry<em>On</em> Admin
            </span>
          </div>
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <span className="dot" />
              {item.label}
            </NavLink>
          ))}
          <div className="side-foot">
            <div className="avatar" />
            <div style={{ flex: 1, minWidth: 0 }}>
              <b>{user.display_name ?? user.email}</b>
              <span>{user.roles.join(", ") || "no role"}</span>
            </div>
            <button className="btn-outline btn-sm" onClick={logout}>
              Out
            </button>
          </div>
        </aside>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </>
  );
}
