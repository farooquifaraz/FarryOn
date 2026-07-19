import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { AuthProvider, Can, NotAnAdminError, useAuth } from "./auth";
import { getImpersonationToken, loadTokens, type ApiRequestError } from "./api";
import { fail, me, ok, rejection, signedIn, stubFetch } from "../test/http";

/**
 * Who is allowed into the panel, and what happens when that changes underneath
 * someone who is already inside.
 *
 * The gate exists because of a real hole: /auth/login is the same endpoint the
 * phone uses, so a correct password let any FarryOn user into the shell — every
 * page an empty table reading "Missing permission: users.read". Nothing leaked
 * (the backend refused all eleven routes) but the login screen promised
 * "Restricted to accounts with an admin role" and wasn't restricting. These
 * tests are what stop that coming back.
 */

/** Renders the auth state as text, and hands the actions back to the test. */
function Probe({ onReady }: { onReady?: (auth: ReturnType<typeof useAuth>) => void }) {
  const auth = useAuth();
  onReady?.(auth);
  return (
    <div>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="user">{auth.user?.email ?? "none"}</span>
      <span data-testid="impersonating">{auth.impersonating?.email ?? "none"}</span>
    </div>
  );
}

/** Mount the provider and wait out the restore-from-storage pass. */
async function mount(): Promise<ReturnType<typeof useAuth>> {
  let auth!: ReturnType<typeof useAuth>;
  render(
    <AuthProvider>
      <Probe onReady={(a) => (auth = a)} />
    </AuthProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
  return auth;
}

const ADMIN = ["dashboard.read", "users.read", "users.write"];

describe("signing in", () => {
  test("an admin gets in", async () => {
    stubFetch((call) =>
      call.url.endsWith("/auth/login")
        ? ok({ access_token: "a", refresh_token: "r" })
        : ok(me(ADMIN)),
    );
    const auth = await mount();

    await act(async () => {
      const res = await auth.login("admin@farryon.app", "pw");
      expect(res.twoFactorRequired).toBe(false);
    });

    expect(screen.getByTestId("user")).toHaveTextContent("admin@farryon.app");
    expect(loadTokens()).not.toBeNull();
  });

  test("a correct password with no admin permission is refused", async () => {
    // The hole this closes: the password was right, so /auth/login answered 200
    // and handed over real tokens. Only the permission check turns that into a
    // refusal.
    stubFetch((call) =>
      call.url.endsWith("/auth/login")
        ? ok({ access_token: "a", refresh_token: "r" })
        : ok(me([], { email: "someone@farryon.app" })),
    );
    const auth = await mount();

    await expect(auth.login("someone@farryon.app", "pw")).rejects.toBeInstanceOf(NotAnAdminError);

    expect(screen.getByTestId("user")).toHaveTextContent("none");
  });

  test("the refused sign-in leaves no tokens behind", async () => {
    // Otherwise the very next reload restores the session and walks straight
    // back into the panel the login just refused.
    stubFetch((call) =>
      call.url.endsWith("/auth/login") ? ok({ access_token: "a", refresh_token: "r" }) : ok(me([])),
    );
    const auth = await mount();

    await auth.login("someone@farryon.app", "pw").catch(() => {});

    expect(loadTokens()).toBeNull();
  });

  test("a user with dashboard.read but nothing else still gets in", async () => {
    // The gate is one permission, not a role name or a level: `manager` belongs
    // here on six permissions, and a future role only has to be granted
    // dashboard.read. Gating on "admin" would lock those out.
    stubFetch((call) =>
      call.url.endsWith("/auth/login")
        ? ok({ access_token: "a", refresh_token: "r" })
        : ok(me(["dashboard.read"], { email: "manager@farryon.app", roles: ["manager"] })),
    );
    const auth = await mount();

    await act(async () => void (await auth.login("manager@farryon.app", "pw")));

    expect(screen.getByTestId("user")).toHaveTextContent("manager@farryon.app");
  });

  test("bad credentials surface as the backend's error, not as NotAnAdmin", async () => {
    // Two different messages for two different problems: "wrong password" and
    // "not allowed here" must not read the same on the sign-in screen.
    stubFetch(() => fail(401, "INVALID_CREDENTIALS", "Incorrect email or password."));
    const auth = await mount();

    const err = await rejection<ApiRequestError>(auth.login("admin@farryon.app", "nope"));

    expect(err).not.toBeInstanceOf(NotAnAdminError);
    expect(err.code).toBe("INVALID_CREDENTIALS");
  });
});

describe("two-factor", () => {
  test("a 2FA challenge stops short of a session", async () => {
    // No tokens, no user — the pending token is not a credential.
    stubFetch(() => ok({ two_factor_required: true, pending_token: "pending-1" }));
    const auth = await mount();

    const res = await auth.login("admin@farryon.app", "pw");

    expect(res).toEqual({ twoFactorRequired: true, pendingToken: "pending-1" });
    expect(loadTokens()).toBeNull();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
  });

  test("a verified code signs the admin in", async () => {
    stubFetch((call) =>
      call.url.endsWith("/2fa/verify-login")
        ? ok({ access_token: "a", refresh_token: "r" })
        : ok(me(ADMIN)),
    );
    const auth = await mount();

    await act(async () => await auth.verify2fa("pending-1", "123456"));

    expect(screen.getByTestId("user")).toHaveTextContent("admin@farryon.app");
  });

  test("the gate applies on the 2FA path too", async () => {
    // The easy place to forget it: a non-admin with 2FA enabled would otherwise
    // walk in through the side door.
    stubFetch((call) =>
      call.url.endsWith("/2fa/verify-login")
        ? ok({ access_token: "a", refresh_token: "r" })
        : ok(me([])),
    );
    const auth = await mount();

    await expect(auth.verify2fa("pending-1", "123456")).rejects.toBeInstanceOf(NotAnAdminError);
    expect(loadTokens()).toBeNull();
  });
});

describe("restoring a session on reload", () => {
  test("an admin comes back signed in", async () => {
    signedIn();
    stubFetch(() => ok(me(ADMIN)));

    await mount();

    expect(screen.getByTestId("user")).toHaveTextContent("admin@farryon.app");
  });

  test("an admin whose role was revoked mid-session is thrown out on reload", async () => {
    // The tokens are still valid — that is the point. Authentication survived;
    // authorisation didn't, and the panel has to re-check rather than trust the
    // session it already holds.
    signedIn();
    stubFetch(() => ok(me([], { email: "demoted@farryon.app" })));

    await mount();

    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(loadTokens()).toBeNull();
  });

  test("no tokens means no /me call at all", async () => {
    const stub = stubFetch(() => ok(me(ADMIN)));

    await mount();

    expect(stub.calls).toHaveLength(0);
    expect(screen.getByTestId("user")).toHaveTextContent("none");
  });

  test("loading stays true until the check finishes, then goes false", async () => {
    // Whatever renders the routes waits on this. If it flipped to false early,
    // the panel would flash the login screen at an admin who is signed in.
    signedIn();
    stubFetch(async () => {
      await new Promise((r) => setTimeout(r, 10));
      return ok(me(ADMIN));
    });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("loading")).toHaveTextContent("true");

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("user")).toHaveTextContent("admin@farryon.app");
  });

  test("an unreachable backend signs nobody in and still stops loading", async () => {
    // A dead backend must not leave the panel on a spinner forever.
    signedIn();
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });

    await mount();

    expect(screen.getByTestId("user")).toHaveTextContent("none");
  });
});

describe("signing out", () => {
  test("clears the session", async () => {
    signedIn();
    stubFetch(() => ok(me(ADMIN)));
    const auth = await mount();

    await act(async () => await auth.logout());

    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(loadTokens()).toBeNull();
  });

  test("clears it even when the logout call fails", async () => {
    // Signing out is a local act. A backend that is down, or a refresh token the
    // server has already forgotten, must not leave someone signed in.
    signedIn();
    stubFetch((call) => (call.url.endsWith("/auth/logout") ? fail(500, "BOOM") : ok(me(ADMIN))));
    const auth = await mount();

    await act(async () => await auth.logout());

    expect(loadTokens()).toBeNull();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
  });
});

describe("permissions in the UI", () => {
  test("can() reflects what the account actually holds", async () => {
    signedIn();
    stubFetch(() => ok(me(ADMIN)));
    const auth = await mount();

    expect(auth.can("users.write")).toBe(true);
    expect(auth.can("billing.write")).toBe(false);
  });

  test("signed out, can() is false rather than throwing", async () => {
    const auth = await mount();

    expect(auth.can("users.read")).toBe(false);
  });

  test("<Can> shows and hides its children", async () => {
    signedIn();
    stubFetch(() => ok(me(ADMIN)));
    render(
      <AuthProvider>
        <Can permission="users.write">
          <button>Suspend</button>
        </Can>
        <Can permission="billing.write">
          <button>Refund</button>
        </Can>
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "Suspend" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Refund" })).not.toBeInTheDocument();
  });
});

describe("impersonation", () => {
  test("starting it swaps the token and names who we became", async () => {
    signedIn();
    stubFetch((call) =>
      call.url.endsWith("/impersonate")
        ? ok({ access_token: "their-token" })
        : ok(me(ADMIN, { email: getImpersonationToken() ? "victim@example.com" : "admin@farryon.app" })),
    );
    const auth = await mount();

    await act(async () => await auth.startImpersonation(42));

    expect(getImpersonationToken()).toBe("their-token");
    expect(screen.getByTestId("impersonating")).toHaveTextContent("victim@example.com");
  });

  test("stopping it hands the admin back their own identity", async () => {
    // The banner comes off `impersonating`. Leaving it set would show a banner
    // for a session that had already ended — or worse, hide that one is live.
    signedIn();
    stubFetch((call) =>
      call.url.endsWith("/impersonate") ? ok({ access_token: "their-token" }) : ok(me(ADMIN)),
    );
    const auth = await mount();
    await act(async () => await auth.startImpersonation(42));

    act(() => auth.stopImpersonation());

    expect(getImpersonationToken()).toBeNull();
    expect(screen.getByTestId("impersonating")).toHaveTextContent("none");
  });

  test("signing out while impersonating ends both", async () => {
    signedIn();
    stubFetch((call) =>
      call.url.endsWith("/impersonate") ? ok({ access_token: "their-token" }) : ok(me(ADMIN)),
    );
    const auth = await mount();
    await act(async () => await auth.startImpersonation(42));

    await act(async () => await auth.logout());

    expect(getImpersonationToken()).toBeNull();
    expect(screen.getByTestId("impersonating")).toHaveTextContent("none");
    expect(screen.getByTestId("user")).toHaveTextContent("none");
  });
});
