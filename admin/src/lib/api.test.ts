import { describe, expect, test } from "vitest";
import { api, ApiRequestError, clearTokens, getImpersonationToken, loadTokens, setImpersonationToken } from "./api";
import { fail, ok, rejection, signedIn, storedTokens, stubFetch, TOKENS } from "../test/http";

/**
 * The API client: envelope unwrapping, the silent refresh, and impersonation.
 *
 * These three are worth testing above everything else in the panel because all
 * of them fail *quietly*. A broken table is obvious. A refresh that fires twice,
 * or an impersonation token that outlives the impersonation, looks exactly like
 * a working panel until the day it doesn't.
 */
describe("responses", () => {
  test("unwraps a success envelope", async () => {
    stubFetch(() => ok({ id: 7, email: "a@b.c" }));

    const res = await api<{ data: { id: number } }>("/api/v1/users/7");

    expect(res.data.id).toBe(7);
  });

  test("an error envelope becomes an ApiRequestError carrying code and status", async () => {
    // The pages show `err.code` — "PERMISSION_DENIED" tells the operator to ask
    // for a role; a bare "Request failed" tells them to ask us.
    stubFetch(() => fail(403, "PERMISSION_DENIED", "Missing permission: users.read"));

    const err = await rejection<ApiRequestError>(api("/api/v1/users"));

    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.code).toBe("PERMISSION_DENIED");
    expect(err.status).toBe(403);
    expect(err.message).toBe("Missing permission: users.read");
  });

  test("success:false with a 200 still throws", async () => {
    // The envelope, not the status line, is the backend's contract. A 200 whose
    // body says success:false must not be handed to a page as data.
    stubFetch(
      () =>
        new Response(
          JSON.stringify({ success: false, data: null, error: { code: "BAD", message: "no" } }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    );

    await expect(api("/api/v1/users")).rejects.toBeInstanceOf(ApiRequestError);
  });

  test("a CSV export comes back as text, not parsed as JSON", async () => {
    stubFetch(() => new Response("id,email\n1,a@b.c\n", {
      status: 200,
      headers: { "content-type": "text/csv" },
    }));

    const csv = await api<string>("/api/v1/audit/export");

    expect(csv).toBe("id,email\n1,a@b.c\n");
  });

  test("a non-JSON failure throws rather than returning the error page as data", async () => {
    // A proxy returning HTML for a 502 must not end up rendered in a table.
    stubFetch(() => new Response("<html>502</html>", {
      status: 502,
      statusText: "Bad Gateway",
      headers: { "content-type": "text/html" },
    }));

    const err = await rejection<ApiRequestError>(api("/api/v1/audit/export"));

    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.status).toBe(502);
  });
});

describe("auth header", () => {
  test("sends the stored access token", async () => {
    signedIn();
    const stub = stubFetch(() => ok({}));

    await api("/api/v1/me");

    expect(stub.calls[0].headers.Authorization).toBe("Bearer access-1");
  });

  test("signed out, sends no Authorization header at all", async () => {
    // Not `Bearer undefined` or `Bearer null` — both would reach the backend as
    // a malformed credential rather than as "anonymous".
    const stub = stubFetch(() => ok({}));

    await api("/api/v1/me");

    expect(stub.calls[0].headers.Authorization).toBeUndefined();
  });
});

describe("silent refresh", () => {
  test("a 401 refreshes once and replays the original request", async () => {
    signedIn();
    const stub = stubFetch((call) => {
      if (call.url.endsWith("/auth/refresh")) {
        return ok({ access_token: "access-2", refresh_token: "refresh-2" });
      }
      return call.headers.Authorization === "Bearer access-2"
        ? ok({ id: 1 })
        : fail(401, "UNAUTHENTICATED");
    });

    const res = await api<{ data: { id: number } }>("/api/v1/me");

    expect(res.data.id).toBe(1);
    expect(stub.to("/auth/refresh")).toHaveLength(1);
    expect(storedTokens()?.access_token).toBe("access-2");
  });

  test("the replay carries the NEW token, not the dead one", async () => {
    // Retrying with the token that just 401'd would loop the user out for no
    // reason and make the refresh pointless.
    signedIn();
    const stub = stubFetch((call) =>
      call.url.endsWith("/auth/refresh")
        ? ok({ access_token: "access-2", refresh_token: "refresh-2" })
        : call.headers.Authorization === "Bearer access-2"
          ? ok({})
          : fail(401, "UNAUTHENTICATED"),
    );

    await api("/api/v1/me");

    const meCalls = stub.to("/api/v1/me");
    expect(meCalls).toHaveLength(2);
    expect(meCalls[1].headers.Authorization).toBe("Bearer access-2");
  });

  test("it refreshes at most once, even when it 401s again", async () => {
    // The guard that matters: without `retried`, a backend that keeps answering
    // 401 would drive an infinite recursion, not a failed request.
    signedIn();
    const stub = stubFetch((call) =>
      call.url.endsWith("/auth/refresh")
        ? ok({ access_token: "access-2", refresh_token: "refresh-2" })
        : fail(401, "UNAUTHENTICATED"),
    );

    await expect(api("/api/v1/me")).rejects.toBeInstanceOf(ApiRequestError);
    expect(stub.to("/auth/refresh")).toHaveLength(1);
    expect(stub.to("/api/v1/me")).toHaveLength(2);
  });

  test("five pages 401-ing at once share one refresh", async () => {
    // The dashboard fires several requests together. Five refreshes would burn
    // four refresh tokens against a family-rotation backend — which is exactly
    // how reuse detection gets tripped and the whole session killed.
    signedIn();
    let refreshed = false;
    const stub = stubFetch(async (call) => {
      if (call.url.endsWith("/auth/refresh")) {
        await new Promise((r) => setTimeout(r, 5));
        refreshed = true;
        return ok({ access_token: "access-2", refresh_token: "refresh-2" });
      }
      return refreshed && call.headers.Authorization === "Bearer access-2"
        ? ok({})
        : fail(401, "UNAUTHENTICATED");
    });

    await Promise.all([
      api("/api/v1/users"),
      api("/api/v1/roles"),
      api("/api/v1/audit"),
      api("/api/v1/billing"),
      api("/api/v1/me"),
    ]);

    expect(stub.to("/auth/refresh")).toHaveLength(1);
  });

  test("a refresh that fails drops the tokens", async () => {
    // The session is over. Keeping the pair would retry it on the next page and
    // on every reload after that.
    signedIn();
    stubFetch((call) =>
      call.url.endsWith("/auth/refresh") ? fail(401, "TOKEN_REVOKED") : fail(401, "UNAUTHENTICATED"),
    );

    await expect(api("/api/v1/me")).rejects.toBeInstanceOf(ApiRequestError);
    expect(loadTokens()).toBeNull();
  });

  test("signed out, a 401 doesn't try to refresh nothing", async () => {
    const stub = stubFetch(() => fail(401, "UNAUTHENTICATED"));

    await expect(api("/api/v1/me")).rejects.toBeInstanceOf(ApiRequestError);
    expect(stub.to("/auth/refresh")).toHaveLength(0);
  });

  test("a later refresh can still run after an earlier one failed", async () => {
    // The single-flight promise has to be cleared in `finally`, not on success.
    // If a failed refresh left it set, every later 401 in the tab would resolve
    // against that dead promise and no session could ever recover.
    signedIn();
    stubFetch((call) => (call.url.endsWith("/auth/refresh") ? fail(401, "NO") : fail(401, "NO")));
    await api("/api/v1/me").catch(() => {});

    signedIn({ access_token: "access-9", refresh_token: "refresh-9" });
    const stub = stubFetch((call) =>
      call.url.endsWith("/auth/refresh")
        ? ok({ access_token: "access-10", refresh_token: "refresh-10" })
        : call.headers.Authorization === "Bearer access-10"
          ? ok({})
          : fail(401, "UNAUTHENTICATED"),
    );

    await api("/api/v1/me");

    expect(stub.to("/auth/refresh")).toHaveLength(1);
    expect(storedTokens()?.access_token).toBe("access-10");
  });
});

describe("impersonation", () => {
  test("requests go out as the impersonated user", async () => {
    signedIn();
    setImpersonationToken("their-token");
    const stub = stubFetch(() => ok({}));

    await api("/api/v1/me");

    expect(stub.calls[0].headers.Authorization).toBe("Bearer their-token");
  });

  test("it is never written to storage", async () => {
    // It must die with the tab. Persisting it would leave an admin holding
    // someone else's identity after a reload, with nothing on screen saying so.
    signedIn();
    setImpersonationToken("their-token");
    stubFetch(() => ok({}));

    await api("/api/v1/me");

    expect(JSON.stringify(localStorage)).not.toContain("their-token");
    expect(storedTokens()).toEqual(TOKENS);
  });

  test("a 401 while impersonating does NOT refresh", async () => {
    // Refreshing here would swap the impersonation token for the admin's own
    // and silently continue the request *as the admin* — the same call, now
    // with far more authority than the operator thought they had.
    signedIn();
    setImpersonationToken("their-token");
    const stub = stubFetch(() => fail(401, "UNAUTHENTICATED"));

    await expect(api("/api/v1/me")).rejects.toBeInstanceOf(ApiRequestError);
    expect(stub.to("/auth/refresh")).toHaveLength(0);
    expect(stub.calls.every((c) => c.headers.Authorization === "Bearer their-token")).toBe(true);
  });

  test("signing out ends the impersonation too", async () => {
    signedIn();
    setImpersonationToken("their-token");

    clearTokens();

    expect(getImpersonationToken()).toBeNull();
    expect(loadTokens()).toBeNull();
  });
});
