/**
 * A stand-in for the network, so the tests can say "the server answers 401
 * once, then 200" without a server.
 *
 * Real `Response` objects rather than hand-rolled fakes: `api()` branches on
 * `res.headers.get("content-type")` to tell a CSV export from an envelope, and
 * a fake that forgets that header would send every test down the JSON path and
 * quietly prove nothing.
 */

import { vi } from "vitest";

export interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface FetchStub {
  /** Every request made, oldest first. */
  calls: Call[];
  /** Requests to a path, e.g. `stub.to("/api/v1/auth/refresh")`. */
  to(path: string): Call[];
}

/** Async so a test can hold a response open — the single-flight refresh can
 *  only be observed while the first refresh is still in the air. */
type Handler = (call: Call, index: number) => Response | Promise<Response>;

export function stubFetch(handler: Handler): FetchStub {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const call: Call = {
        url: String(url),
        method: init.method ?? "GET",
        headers: (init.headers as Record<string, string>) ?? {},
        body: init.body ? JSON.parse(init.body as string) : undefined,
      };
      calls.push(call);
      return handler(call, calls.length - 1);
    }),
  );
  return {
    calls,
    to: (path: string) => calls.filter((c) => c.url.endsWith(path)),
  };
}

/** The backend's success envelope. */
export function ok(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ success: true, data, error: null }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** The backend's error envelope — note it carries a code, which the panel
 *  shows to the operator and which several branches switch on. */
export function fail(status: number, code: string, message = code): Response {
  return new Response(
    JSON.stringify({ success: false, data: null, error: { code, message } }),
    { status, headers: { "content-type": "application/json" } },
  );
}

/** A user as /api/v1/me returns one. `permissions` is what the panel gates on. */
export function me(permissions: string[], over: Record<string, unknown> = {}) {
  return {
    id: 1,
    email: "admin@farryon.app",
    display_name: "Admin",
    status: "active",
    email_verified: true,
    roles: ["admin"],
    permissions,
    ...over,
  };
}

/** The rejection, typed. `.catch(e => e)` widens to a union with whatever the
 *  promise resolves to, so every use site would need a cast; say it once here. */
export async function rejection<E = Error>(p: Promise<unknown>): Promise<E> {
  return p.then(
    () => {
      throw new Error("expected a rejection, got a value");
    },
    (e) => e as E,
  );
}

export const TOKENS = { access_token: "access-1", refresh_token: "refresh-1" };

export function signedIn(pair = TOKENS): void {
  localStorage.setItem("farryon_admin_tokens", JSON.stringify(pair));
}

export function storedTokens(): { access_token: string; refresh_token: string } | null {
  const raw = localStorage.getItem("farryon_admin_tokens");
  return raw ? JSON.parse(raw) : null;
}
