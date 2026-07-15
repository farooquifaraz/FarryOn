# Admin & User Module — Architecture (Modular Monolith)

Status: approved direction, pre-implementation. Supersedes/extends the earlier auth plan at `docs/../.claude/plans/mujhe-farry-app-k-compiled-goblet.md` with full-scope decisions below.

## 0. Decisions locked in

| Question | Decision | Why |
|---|---|---|
| Feature scope | **Full spec**: password auth, RBAC, 2FA (TOTP), SSO (Google/Microsoft), impersonation, session mgmt, audit logs, subscriptions/revenue | User confirmed enterprise-grade scope over trimmed core |
| Redis / Celery | **Skip for v1.** Replace with DB-backed rate limiting + short-TTL in-process cache for revocation checks + APScheduler/FastAPI `BackgroundTasks` for jobs | Render free/starter tier has no Redis add-on without extra cost; app runs as a single instance today, so a distributed cache isn't load-bearing yet. Upgrade path documented below. |
| Admin frontend | **React + Vite SPA** (not Next.js) | No SSR/SEO need — it's a login-gated internal tool. Simpler build, matches existing Midnight Aurora / farryon-website design tokens more directly than Next.js's routing conventions would require. |
| Migrations | **Add Alembic** | Repo currently relies on `create_all()`; a real user/auth/RBAC schema needs versioned migrations from day one. |

### Where this diverges from the source document, and the trade-off accepted
- **No Redis-backed refresh-token reuse detection across instances.** Reuse detection still works (hashed refresh tokens in Postgres, rotation + family revocation), but the *access-token* denylist (for instant force-logout) becomes a DB lookup (`users.tokens_revoked_before` timestamp, checked once per request against `iat`) instead of a Redis `SETEX`. Slightly higher DB load per request; fine at current scale. **Upgrade trigger: move to Redis once running >1 backend instance or >~50 req/s.**
- **No Celery/ARQ job queue.** Email sending (verification, reset, invite) and the account-anonymization sweep run via APScheduler (in-process cron) + `BackgroundTasks` for fire-and-forget sends. **Upgrade trigger: move to ARQ+Redis once background jobs need retries-with-backoff, multi-worker fan-out, or job volume grows past what one process comfortably handles.**
- **Rate limiting is DB-backed** (`login_attempts` table, windowed count query) instead of Redis sliding-window. Good enough for current auth traffic; document the swap point in `DECISIONS.md` when built.

These are documented explicitly so a future "scale up" pass has a clear checklist rather than a silent gap.

---

## 1. Backend — modular monolith layout

```
backend/app/
  core/
    config.py            # existing — extend with jwt/oauth/2fa settings
    security.py           # argon2id hashing, JWT encode/decode, TOTP verify
    deps.py                # get_current_user, require_permission("x.y"), require_role
    rate_limit.py          # DB-backed windowed limiter, dependency-injectable
    exceptions.py          # AppError -> stable error codes, uniform envelope
    logging.py             # structlog config (already exists, extend with request_id)

  db/
    base.py                 # existing declarative base + session factory
    migrations/             # NEW: alembic/ (versions/, env.py, alembic.ini)

  modules/                                    # one folder per bounded context
    auth/
      models.py             # RefreshToken, EmailVerificationToken, PasswordResetToken
      schemas.py             # Pydantic request/response
      service.py              # register/login/refresh/logout/verify/reset logic
      router.py                # /auth/* endpoints
    twofa/
      models.py               # TotpSecret, RecoveryCode
      service.py                # enroll/verify/disable, recovery code issue+consume
      router.py                  # /auth/2fa/*
    sso/
      service.py                # authlib Google + Microsoft OIDC flows, account linking
      router.py                  # /auth/sso/{provider}/*
    rbac/
      models.py               # Role, Permission, RolePermission, UserRole
      service.py                # role/permission CRUD, guard rails (last-super-admin, etc.)
      router.py                  # /roles, /permissions
      seed.py                     # seed script: default roles + first super_admin (env-driven)
    users/
      models.py                # extends existing User: status, soft-delete, profile fields
      service.py                 # CRUD, invite flow, bulk actions, CSV export
      router.py                   # /users, /users/{id}/roles
    sessions/
      models.py               # UserSession (device/UA/IP/coarse-location/timestamps)
      service.py                 # list/revoke own or admin-revoke
      router.py                   # /users/{id}/sessions, /me/sessions
    impersonation/
      service.py               # start/stop, `act` claim minting, role-level guard
      router.py                  # /users/{id}/impersonate
    audit/
      models.py               # AuditLog (append-only, before/after JSON diff)
      service.py                 # write_audit(...) helper called from other modules
      router.py                   # /audit-logs (read-only, filter/search/export)
    billing/                                     # from the earlier approved plan
      models.py               # Plan, Subscription, Payment
      service.py                 # revenue aggregation, webhook handlers
      router.py                   # /admin/subscriptions, /admin/revenue/*, /webhooks/{provider}
    settings/
      models.py               # SystemSetting (key/value, typed)
      service.py
      router.py                   # /admin/settings
    dashboard/
      service.py               # KPI aggregation queries
      router.py                   # /admin/dashboard/stats
    me/
      router.py                   # /me/* profile, password, email, sessions, 2fa (thin, delegates to above services)

  api/
    v1.py                  # aggregates all module routers under /api/v1, mounts OpenAPI

  workers/
    scheduler.py            # APScheduler: expired-token cleanup, anonymization sweep, session GC

  main.py                    # existing app factory — mounts api/v1, CORS, security headers middleware
```

**Cross-cutting rules, enforced once and reused everywhere:**
- Every module's `router.py` uses `Depends(require_permission("module.action"))` from `core/deps.py` — never re-implements auth checks.
- Every mutating endpoint calls `audit.service.write_audit(...)` — enforced by a lightweight decorator or explicit call in the service layer, not left to each router to remember.
- Every response uses the uniform envelope from `core/exceptions.py` / a shared `APIResponse` Pydantic generic.

This mirrors the module boundaries already implicit in the existing codebase (`backend/app/db/repo.py`, `backend/app/ws/live.py` as separate concerns) — same pattern, just applied consistently to the new surface area.

---

## 2. Database additions (Alembic-managed from the first migration)

`users` (extend existing model): `email`, `password_hash`, `status` (active/invited/suspended/deactivated), `deleted_at`, `tokens_revoked_before`, `avatar_url`, `timezone`, `locale`, partial-unique index on `email WHERE deleted_at IS NULL`.

New tables: `roles`, `permissions`, `role_permissions`, `user_roles`, `refresh_tokens` (hashed, family_id for reuse detection), `email_verification_tokens`, `password_reset_tokens`, `totp_secrets`, `recovery_codes` (hashed), `oauth_accounts` (provider, provider_user_id, linked user_id), `user_sessions`, `audit_logs`, `login_attempts` (rate-limit windowing), `plans`, `subscriptions`, `payments`, `system_settings`.

---

## 3. Frontend — `admin/` (React + Vite + TypeScript)

```
admin/src/
  app/
    router.tsx              # route tree, layout-level guards
    routes/
      auth/ (login, 2fa-verify, sso-callback, forgot/reset password)
      users/ (list, detail)
      roles/
      sessions/
      audit/
      billing/ (subscriptions & revenue — matches the mockup shipped earlier)
      settings/
      dashboard/
  components/
    Can.tsx                  # <Can permission="users.delete">...</Can>, backend is still source of truth
    DataTable/                 # TanStack Table wrapper — server pagination/sort/filter, URL-synced
    ImpersonationBanner.tsx
  lib/
    api-client.ts            # fetch wrapper, silent refresh, envelope unwrapping, error-code mapping
    auth-context.tsx          # current user, permissions, impersonation state
  styles/
    tokens.css                # ported from farryon-website_1.html (--bg/--pl/--ac/--gold/--fd/--fb/--fm)
```

Visual language: same tokens already used in the mockup delivered earlier (dark navy/teal/purple, Space Grotesk + Inter + JetBrains Mono, pill buttons, glass cards) — the mockup becomes the literal build target for these screens, not a new design pass.

---

## 4. Delivery phases (extends the earlier plan; commit per phase)

1. Alembic setup + schema for `users`/`roles`/`permissions`/`refresh_tokens` + seed script (roles, permissions, first super_admin via env)
2. Core auth: register/login/refresh/logout, email verify, password reset (DB-backed rate limiting from the start)
3. RBAC engine + `require_permission` dependency + roles/permissions API + guard rails (last-super-admin, self-role-edit block)
4. User management: CRUD, soft delete, invite flow, bulk actions, CSV export
5. Sessions (device tracking + revocation) + 2FA (TOTP + recovery codes) + SSO (Google, Microsoft via authlib)
6. Impersonation + audit logging wired into every mutating endpoint from phases 2–5
7. Billing module (plans/subscriptions/payments/webhooks) — as previously scoped
8. Frontend: auth screens → admin user list/detail → roles UI → sessions/2FA → audit viewer → billing → dashboard → settings
9. Hardening: security headers, CSP, CORS allowlist, structured logging with request_id, tests (pytest + httpx + testcontainers-postgres for backend; vitest + RTL + one Playwright e2e for frontend), CI updated to run Postgres service container

---

## 5. API contract

Adopt the document's envelope as-is:
```json
{ "success": true, "data": {}, "error": null, "meta": { "page": 1, "page_size": 20, "total": 0 } }
```
Errors: `{ "success": false, "error": { "code": "USER_SUSPENDED", "message": "...", "fields": {} } }`. Offset pagination (simpler to reason about at current scale; revisit cursor pagination if user tables grow past ~100k rows).

---

## 6. Infra changes needed

- `docker-compose.yml`: no new services needed for v1 (Redis/worker deferred per decision above); keep `backend`, `postgres`, `prometheus`, `grafana`; add `admin` (Vite dev server) for local dev.
- `render.yaml`: add a Postgres instance if not already provisioned for prod (currently defaults to ephemeral SQLite per its own comments) — **this must happen before any real user data exists**, independent of this module. Add a second Render **Static Site** service for the `admin/` build output.
- `.github/workflows/ci.yml`: add a `postgres:16` service container so RBAC/auth integration tests run against real Postgres, not SQLite (SQLite doesn't enforce all the constraints — partial unique indexes, etc. — this schema needs).
