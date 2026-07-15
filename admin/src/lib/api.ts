/**
 * API client: envelope unwrapping, bearer auth, and silent token refresh.
 *
 * Tokens live in localStorage under one key. The architecture doc calls for
 * an httpOnly refresh cookie; the backend currently returns the refresh
 * token in the JSON body (mobile app needs that too), so the SPA stores it —
 * an accepted deviation until a cookie mode is added server-side.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
const STORAGE_KEY = "farryon_admin_tokens";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

export interface ApiError {
  code: string;
  message: string;
  fields?: Record<string, string>;
}

export class ApiRequestError extends Error {
  code: string;
  status: number;
  constructor(status: number, err: ApiError) {
    super(err.message);
    this.code = err.code;
    this.status = status;
  }
}

// Impersonation: a separate, in-memory-only token that temporarily replaces
// the admin's own token for requests. Never persisted — closing the tab ends it.
let impersonationToken: string | null = null;

export function setImpersonationToken(token: string | null): void {
  impersonationToken = token;
}
export function getImpersonationToken(): string | null {
  return impersonationToken;
}

export function loadTokens(): TokenPair | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw ? (JSON.parse(raw) as TokenPair) : null;
}
export function saveTokens(pair: TokenPair): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(pair));
}
export function clearTokens(): void {
  localStorage.removeItem(STORAGE_KEY);
  impersonationToken = null;
}

let refreshPromise: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  // Single-flight: many 401s at once should trigger exactly one refresh.
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const tokens = loadTokens();
      if (!tokens) return false;
      const res = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: tokens.refresh_token }),
      });
      if (!res.ok) {
        clearTokens();
        return false;
      }
      const body = await res.json();
      saveTokens(body.data);
      return true;
    })().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown; retried?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const bearer = impersonationToken ?? loadTokens()?.access_token;
  if (bearer) headers["Authorization"] = `Bearer ${bearer}`;

  const res = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (res.status === 401 && !options.retried && !impersonationToken) {
    if (await tryRefresh()) {
      return api<T>(path, { ...options, retried: true });
    }
  }

  // CSV export and similar non-JSON responses.
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    if (!res.ok) throw new ApiRequestError(res.status, { code: "HTTP_ERROR", message: res.statusText });
    return (await res.text()) as unknown as T;
  }

  const body = await res.json();
  if (!res.ok || body.success === false) {
    const err: ApiError = body.error ?? { code: "HTTP_ERROR", message: res.statusText };
    throw new ApiRequestError(res.status, err);
  }
  return body as T;
}

export interface Envelope<T> {
  success: boolean;
  data: T;
  error: ApiError | null;
  meta?: { page: number; page_size: number; total: number };
}
