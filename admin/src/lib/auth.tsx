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
      if (loadTokens()) setUser(await fetchMe());
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
      setUser(await fetchMe());
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
      setUser(await fetchMe());
    },
    [fetchMe],
  );

  const logout = useCallback(async () => {
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
      setImpersonating(await fetchMe());
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
