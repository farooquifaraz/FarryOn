/** Auth context: current user, permissions, login/logout, impersonation state. */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  clearTokens,
  loadTokens,
  saveTokens,
  setImpersonationToken,
  type Envelope,
} from "./api";

export interface Me {
  id: number;
  email: string | null;
  display_name: string | null;
  status: string;
  email_verified: boolean;
  roles: string[];
  permissions: string[];
}

interface LoginResult {
  twoFactorRequired: boolean;
  pendingToken?: string;
}

interface AuthState {
  user: Me | null;
  loading: boolean;
  impersonating: Me | null;
  login: (email: string, password: string) => Promise<LoginResult>;
  verify2fa: (pendingToken: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
  startImpersonation: (userId: number) => Promise<void>;
  stopImpersonation: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

/** Whether this account may use the admin panel at all.
 *
 * Everyone with a FarryOn login can authenticate here — /auth/login is the same
 * endpoint the phone uses — so a correct password is not, by itself, permission
 * to be in this building. Without this check a plain user signed in and browsed
 * the whole shell: sidebar, dashboard, "Welcome back", every page reporting
 * "Missing permission: users.read" over an empty table. Nothing leaked (the
 * backend refused all eleven admin routes) but the login screen promises
 * "Restricted to accounts with an admin role", and it wasn't restricting.
 *
 * The gate is a permission, not a role name or level: `manager` legitimately
 * belongs here with 6 permissions while `user` has none, and a future role only
 * has to be granted `dashboard.read` to work. This is UI only — every route the
 * panel calls is still enforced server-side by require_permission.
 */
const PANEL_PERMISSION = "dashboard.read";

function mayUsePanel(me: Me | null): boolean {
  return me?.permissions.includes(PANEL_PERMISSION) ?? false;
}

/** Thrown by `login`/`verify2fa` when the credentials are right but the account
 *  has no business here. Carries the message the sign-in screen shows. */
export class NotAnAdminError extends Error {
  constructor() {
    super("This account doesn't have admin access.");
    this.name = "NotAnAdminError";
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [impersonating, setImpersonating] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchMe = useCallback(async (): Promise<Me | null> => {
    try {
      const res = await api<Envelope<Me>>("/api/v1/me");
      return res.data;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    (async () => {
      if (loadTokens()) {
        // Re-check on every reload, not just at login: an admin whose role was
        // revoked while they had the tab open must not walk back in with the
        // session they already hold.
        const me = await fetchMe();
        if (mayUsePanel(me)) setUser(me);
        else clearTokens();
      }
      setLoading(false);
    })();
  }, [fetchMe]);

  const login = useCallback(
    async (email: string, password: string): Promise<LoginResult> => {
      const res = await api<Envelope<Record<string, unknown>>>("/api/v1/auth/login", {
        method: "POST",
        body: { email, password },
      });
      if (res.data.two_factor_required) {
        return { twoFactorRequired: true, pendingToken: res.data.pending_token as string };
      }
      saveTokens({
        access_token: res.data.access_token as string,
        refresh_token: res.data.refresh_token as string,
      });
      const me = await fetchMe();
      if (!mayUsePanel(me)) {
        // Drop the tokens rather than keep a session that can't do anything —
        // otherwise a reload would walk straight back into the empty panel.
        clearTokens();
        throw new NotAnAdminError();
      }
      setUser(me);
      return { twoFactorRequired: false };
    },
    [fetchMe],
  );

  const verify2fa = useCallback(
    async (pendingToken: string, code: string) => {
      const res = await api<Envelope<{ access_token: string; refresh_token: string }>>(
        "/api/v1/auth/2fa/verify-login",
        { method: "POST", body: { pending_token: pendingToken, code } },
      );
      saveTokens(res.data);
      const me = await fetchMe();
      if (!mayUsePanel(me)) {
        clearTokens();
        throw new NotAnAdminError();
      }
      setUser(me);
    },
    [fetchMe],
  );

  const logout = useCallback(async () => {
    // Drop any impersonation token BEFORE the logout call, or `api()` sends the
    // victim's bearer (it prefers the impersonation token) while the body
    // carries the admin's refresh token — the server then sees the admin's
    // token being revoked by the impersonated user, and depending on
    // enforcement either revokes under the wrong principal or rejects it,
    // leaving the admin's refresh token alive after a "successful" logout.
    setImpersonationToken(null);
    const tokens = loadTokens();
    if (tokens) {
      try {
        await api("/api/v1/auth/logout", {
          method: "POST",
          body: { refresh_token: tokens.refresh_token },
        });
      } catch {
        /* best-effort */
      }
    }
    clearTokens();
    setUser(null);
    setImpersonating(null);
  }, []);

  const can = useCallback(
    (permission: string) => user?.permissions.includes(permission) ?? false,
    [user],
  );

  const startImpersonation = useCallback(
    async (userId: number) => {
      const res = await api<Envelope<{ access_token: string }>>(
        `/api/v1/users/${userId}/impersonate`,
        { method: "POST" },
      );
      setImpersonationToken(res.data.access_token);
      // `fetchMe` swallows every error and returns null. If it fails here — a
      // blip, a 500, the fresh token momentarily rejected — we must NOT leave
      // the impersonation token set: `impersonating` would be null so no banner
      // shows, yet every later request would still go out as the victim. Clear
      // it and fail loudly instead of browsing as someone else in silence.
      const who = await fetchMe();
      if (!who) {
        setImpersonationToken(null);
        setImpersonating(null);
        throw new Error("Could not start impersonation — please try again.");
      }
      setImpersonating(who);
    },
    [fetchMe],
  );

  const stopImpersonation = useCallback(() => {
    setImpersonationToken(null);
    setImpersonating(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, loading, impersonating, login, verify2fa, logout, can, startImpersonation, stopImpersonation }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

/** Render children only when the signed-in admin holds `permission`.
 * UI hiding only — the backend's require_permission is the source of truth. */
export function Can({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth();
  return can(permission) ? <>{children}</> : null;
}
