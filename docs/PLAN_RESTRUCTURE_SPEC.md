# FarryOn — Plan & Feature Restructure — Implementation Spec

**Purpose:** hand this file to Claude Code. It fully specifies the changes to move
FarryOn to **monthly** plan quotas, gate paid-only features, and align the
marketing site. Every change is file- and function-scoped with before/after code.

> Repo root: `D:/FarryOn`. Backend: `D:/FarryOn/backend` (Python/FastAPI, venv at
> `backend/.venv`). Run tests with `backend/.venv/Scripts/python.exe -m pytest`.
> Site: `backend/app/web/index.html` (served at `/`).

---

## 0. Final plan × feature matrix (source of truth)

| | **Free** (trial) | **Lite $5** | **Plus $10** | **Pro $20** |
|---|---|---|---|---|
| Voice | 60 min (one-time, lifetime) | 150 min / month | 360 min / month | 900 min / month |
| Camera vision scans | 0 (blocked) | 25 / month | 40 / month | Unlimited |
| Web searches | 0 (blocked) | 100 / month | 300 / month | Unlimited |
| Real-time Voice AI | ✓ | ✓ | ✓ | ✓ |
| Live Translation (20+ langs) | ✓ | ✓ | ✓ | ✓ |
| Voice Notes & transcription | ✓ | ✓ | ✓ | ✓ |
| Reminders & Tasks | ✓ | ✓ | ✓ | ✓ |
| See & Ask (camera vision) | ✗ | ✓ | ✓ | ✓ |
| WhatsApp & Telegram | ✗ | ✓ | ✓ | ✓ |
| Email by Voice (Gmail + official, 2 accounts) | ✗ | ✓ | ✓ | ✓ |
| Personal AI Memory | ✗ | ✓ | ✓ | ✓ |

**Rules**
- **Voice**: Free is a **lifetime** 60-min trial (already implemented). Lite/Plus/Pro
  are enforced as a **calendar-month** budget (NOT per-day).
- **Vision scans & Web searches**: enforced per **calendar month**. `0` = blocked, `-1` = unlimited.
- **Gated features** (See&Ask vision, WhatsApp, Telegram, Email, Memory): blocked on
  Free with a friendly "upgrade" message; available on any paid plan.
- No "Priority responses / Priority support" — removed.

---

## 1. `backend/app/config.py` — catalog + gating + monthly derivation

### 1a. Update `plan_catalog` numbers
Find the `plan_catalog` field default and replace the four rows with:

```python
    plan_catalog: dict[str, dict[str, float | int | str]] = Field(
        default_factory=lambda: {
            #        price  period     voice_min  scans  searches
            "free": {"price_usd": 0.0,  "period": "trial", "voice_minutes": 60,  "image_scans": 0,  "web_searches": 0},    # noqa: E501
            "lite": {"price_usd": 5.0,  "period": "month", "voice_minutes": 150, "image_scans": 25, "web_searches": 100},  # noqa: E501
            "plus": {"price_usd": 10.0, "period": "month", "voice_minutes": 360, "image_scans": 40, "web_searches": 300},  # noqa: E501
            "pro":  {"price_usd": 20.0, "period": "month", "voice_minutes": 900, "image_scans": -1, "web_searches": -1},   # noqa: E501
        }
    )
```

### 1b. Add the feature-gating map (new field, near `plan_catalog`)

```python
    # Paid-only features. Each maps to the MINIMUM plan (by catalog order) that
    # unlocks it; anything below is blocked with an "upgrade" message. Order is
    # taken from plan_catalog keys: free < lite < plus < pro. "lite" therefore
    # means "any paid plan".
    gated_features: dict[str, str] = Field(
        default_factory=lambda: {
            "vision": "lite",     # See & Ask camera scans (also enforced by image_scans=0 on free)
            "whatsapp": "lite",
            "telegram": "lite",
            "email": "lite",
            "memory": "lite",
        }
    )
```

### 1c. Monthly voice: change `_derive_plan_limits`
The cap is now the **full monthly budget** (not divided by days). Replace the body of
`_derive_plan_limits` so BOTH trial and month plans use `minutes * 60`:

```python
    def _derive_plan_limits(self) -> dict[str, dict[str, int]]:
        """Quota caps derived from :attr:`plan_catalog`.

        * ``trial`` — voice cap = the whole one-time LIFETIME budget
          (``voice_minutes * 60``); metered against the user's all-time voice.
        * ``month`` — voice cap = the whole MONTHLY budget (``voice_minutes * 60``);
          metered against the current calendar month.

        ``image_scans`` / ``web_searches`` pass through as per-month caps.
        """
        out: dict[str, dict[str, int]] = {}
        for name, p in self.plan_catalog.items():
            minutes = int(p.get("voice_minutes", 0))
            out[name] = {
                "voice_seconds": minutes * 60,
                "image_scans": int(p.get("image_scans", 0)),
                "web_searches": int(p.get("web_searches", 0)),
            }
        return out
```

- `days_per_month` is now unused for derivation. Leave the field (harmless) or delete it
  and its one reference; if deleting, also drop it from any docstring.

### 1d. Add plan-rank + feature helpers on `Settings`
Add these methods (next to `is_trial_plan`):

```python
    def plan_rank(self, name: str | None) -> int:
        """Ordinal of a plan in the catalog (free=0, lite=1, ...). Unknown → 0."""
        order = list(self.plan_catalog.keys())
        name = name or self.default_plan
        return order.index(name) if name in order else 0

    def feature_allowed(self, plan: str | None, feature: str) -> bool:
        """True if ``plan`` unlocks ``feature`` (min-tier from gated_features)."""
        min_plan = self.gated_features.get(feature)
        if not min_plan:
            return True  # not a gated feature
        return self.plan_rank(plan) >= self.plan_rank(min_plan)
```

---

## 2. Monthly enforcement — period key `YYYY-MM-DD` → `YYYY-MM`

The usage table (`DailyUsage`, PK `(user_key, day)`, `day` is `String(10)`) already fits
a 7-char `"YYYY-MM"` key. **No DB migration.** Old daily rows simply go stale and are
ignored. Change every place that writes/reads the metering period to use the month.

### 2a. `backend/app/tools/quota.py`
Replace `_today()` with a month period, and update both usages in `check_quota`:

```python
def _period() -> str:
    """Current metering period key — calendar month, ``YYYY-MM`` (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")
```

- In `check_quota`, replace `_today()` with `_period()` in BOTH spots (the unlimited-record
  branch and `key, day = _user_key(ctx), _period()`). Keep the local var name `day` or rename
  to `period` — cosmetic.
- Remove the now-unused `_today` (or keep as an alias if referenced elsewhere — grep first:
  `grep -rn "_today" backend/app`).

### 2b. `backend/app/ws/session.py`
Voice metering must use the month period for **paid** plans. Trial (Free) already uses
lifetime and is unaffected. Two spots use `strftime("%Y-%m-%d")`:

- `_flush_voice_usage`: change `day=datetime.now(timezone.utc).strftime("%Y-%m-%d")`
  → `day=datetime.now(timezone.utc).strftime("%Y-%m")`.
- `_load_voice_usage`: same change on the `get_daily_usage(... day=...)` call.

Leave the trial branch (`is_trial_plan` → `repo.lifetime_voice_seconds`) exactly as-is —
lifetime already sums across every period regardless of the key format.

> Add a one-line comment at each site: `# metering period = calendar month (YYYY-MM)`.

### 2c. `backend/app/db/models.py`
Update the `DailyUsage` docstring only (no schema change): note `day` now holds the
**metering period** — `YYYY-MM` for monthly caps (the free voice trial is metered
lifetime, summed across periods). Column stays `String(10)`.

---

## 3. Feature gating (real enforcement) — `check_feature`

### 3a. Add `check_feature` to `backend/app/tools/quota.py`
Mirrors `check_quota`'s ctx/plan resolution; returns `None` when allowed, else an
upsell result the model speaks.

```python
async def check_feature(ctx: ToolContext, feature: str) -> dict[str, Any] | None:
    """Gate a paid-only feature. None when allowed; an ``upgrade_required`` dict
    (which the model reads aloud) when the caller's plan is too low.

    No-op when enforcement is off or there is no session/user to resolve a plan
    against — same fail-open rule as :func:`check_quota` (gating protects upsell,
    not the bill; never break a tool over missing plumbing)."""
    settings = get_settings()
    if not settings.quota_enforcement_enabled or ctx.session is None:
        return None
    plan = await _plan_for(ctx)
    if settings.feature_allowed(plan, feature):
        return None
    min_plan = settings.gated_features.get(feature, "lite")
    label = feature.replace("_", " ")
    logger.info("feature.gated", user_key=_user_key(ctx), feature=feature, plan=plan)
    return {
        "ok": False,
        "status": "upgrade_required",
        "message": (
            f"{label.title()} is available on the {min_plan.title()} plan and up. "
            "Upgrade in the app to use it."
        ),
    }
```

### 3b. Gate the paid-only tools
At the **top of each `run(self, ctx, **kwargs)`** below, add:

```python
        gate = await check_feature(ctx, "<FEATURE>")
        if gate is not None:
            return gate
```

Add the import `from app.tools.quota import check_feature` (or extend the existing
`from app.tools.quota import check_quota` line) in each file.

| File | `run()` at | `<FEATURE>` |
|---|---|---|
| `backend/app/tools/whatsapp.py` | ~line 95 | `"whatsapp"` |
| `backend/app/tools/telegram.py` | ~line 105 | `"telegram"` |
| `backend/app/tools/email_send.py` | ~line 99 | `"email"` |
| `backend/app/tools/email_read.py` | ~lines 288 AND 395 (both run methods) | `"email"` |
| `backend/app/tools/recall.py` | ~lines 36, 73, 118 (all three run methods) | `"memory"` |

> Vision (`identify.py`) does not need a `check_feature` call — it is already gated by
> `image_scans = 0` on Free via the existing `check_quota(ctx, "image_scans")` at
> `identify.py:75`, which returns "not available on your current plan" when the cap is 0.
> (Optional consistency: you MAY also add `check_feature(ctx, "vision")` there for a nicer
> "upgrade" wording; if you do, place it before the `check_quota` call.)

---

## 4. Website — `backend/app/web/index.html`

The site is served from disk each request (no rebuild). Only the **pricing plan cards**
change; the feature grid and spotlight are already correct. In the `#pricing` section
(`<div class="plans" ...>`), replace the four `.plan` cards' details as follows. Keep the
existing `.plan` / `.plan-inner` / `.pv` (data-m/data-a) / `.plan-btn` structure and the
Monthly↔Annual toggle. **Remove all "Priority responses / Priority support" lines.**

**Free** — amount `$0`, period `one-time trial`:
```html
<ul class="plan-feats">
  <li>60 minutes of voice (one-time)</li>
  <li>Voice AI, translation, notes &amp; reminders</li>
  <li class="dim">No camera vision</li>
  <li class="dim">No web search</li>
  <li>No credit card needed</li>
</ul>
```
**Lite** — `$5` (`data-m="5" data-a="4"`), `per month` / `billed $48/yr`:
```html
<ul class="plan-feats">
  <li>150 minutes of voice / month</li>
  <li>25 camera scans / month</li>
  <li>100 web searches / month</li>
  <li>WhatsApp &amp; Telegram</li>
  <li>Email by voice — Gmail + official (any provider)</li>
  <li>Personal AI memory</li>
</ul>
```
**Plus** — `$10` (`data-m="10" data-a="8"`), `per month` / `billed $96/yr`, keep `Most popular`:
```html
<ul class="plan-feats">
  <li>360 minutes of voice / month</li>
  <li>40 camera scans / month</li>
  <li>300 web searches / month</li>
  <li>Everything in Lite</li>
</ul>
```
**Pro** — `$20` (`data-m="20" data-a="16"`), `per month` / `billed $192/yr`:
```html
<ul class="plan-feats">
  <li>900 minutes of voice / month</li>
  <li>Unlimited camera scans</li>
  <li>Unlimited web searches</li>
  <li>Everything in Plus</li>
</ul>
```

Glasses hardware block (L801 AED 300 / L802 AED 350) and the sub-text stay unchanged.

---

## 5. Tests — `backend/tests/`

Run the suite; fix the fallout from (i) new numbers, (ii) monthly period, (iii) gating.
**Delete the stale dev DB first** (`rm -f backend/farryon.db`) — it causes unrelated
`table already exists` errors on Windows.

### 5a. `test_voice_quota.py`
- The metering period is now the month. Change the period constant:
  `_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")`
  → `_PERIOD = datetime.now(timezone.utc).strftime("%Y-%m")` and use it in every
  `bump_daily_usage(..., day=_PERIOD)` / `get_daily_usage(..., day=_PERIOD)` call.
- `test_yesterday_does_not_count_against_today`: retarget to "last month doesn't count
  against this month" — write the old row under a *different month* key (e.g.
  `"2020-01"`) and assert it is ignored. Rename accordingly.
- `cap_of` still injects `settings.plan_limits["test-plan"]` — unchanged and fine.

### 5b. `test_quota.py`
- Free `image_scans` is now **0** (was 2). Update any test asserting the free image cap,
  and the "unknown plan falls back to default" test if it relied on the old free numbers.
- Confirm `plan_cap("web_searches", "free") == 0` and a `check_quota` on a `0` cap returns
  `quota_exceeded` (the `cap == 0` branch already does this).

### 5c. New — `test_feature_gating.py`
Cover `Settings.feature_allowed` and `check_feature`:
- `feature_allowed("free","whatsapp") is False`; `feature_allowed("lite","whatsapp") is True`;
  `feature_allowed("pro","email") is True`; a non-gated feature → always True.
- `check_feature` returns `None` when enforcement is off, when `ctx.session is None`, and for
  a paid plan; returns an `upgrade_required` dict for a free-plan ctx. Mirror the fixtures in
  `test_quota.py` (SimpleNamespace settings + a fake ToolContext).

### 5d. `test_seed_plans.py`
Prices are unchanged ($5/$10/$20) so `sold_plans()` still seeds lite/plus/pro at
500/1000/2000. No change expected — just confirm it passes.

### 5e. Derived-values sanity (add to an existing config/quota test)
Assert the monthly derivation: `plan_limits["lite"]["voice_seconds"] == 9000`
(150 min), `plus == 21600`, `pro == 54000`, and `image_scans`/`web_searches` match
the catalog (free 0/0, lite 25/100, plus 40/300, pro -1/-1).

---

## 6. Acceptance checklist

- [ ] `plan_catalog` shows free 60/0/0, lite 150/25/100, plus 360/40/300, pro 900/-1/-1.
- [ ] `plan_limits["<plan>"]["voice_seconds"]` == `voice_minutes*60` for every plan.
- [ ] A signed-in **free** user: voice capped at 60 min lifetime; `identify_image`,
      `send_whatsapp`, `send_telegram`, email tools, and memory tools all return an
      `upgrade_required` / `quota_exceeded` message (never execute).
- [ ] A **lite** user: 150 voice min/month, 25 vision/month, 100 web/month; WhatsApp,
      Telegram, Email (2 mailboxes), Memory all work.
- [ ] Voice/vision/web usage resets at the start of a new calendar month.
- [ ] Website `#pricing`: monthly wording, Free shows ✗ vision/web, gated features per
      matrix, email "Gmail + official (any provider)", NO priority rows.
- [ ] `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` is green
      (delete `backend/farryon.db` first).

---

## 7. Notes / gotchas
- **Fail-open stays:** `check_quota` and `check_feature` return `None` (allow) when there is
  no session/DB — don't "fix" this; it protects live calls from plumbing errors.
- **Trial voice is lifetime, not monthly** — do not route Free voice through the monthly
  period; the `is_trial_plan` branch in `_load_voice_usage` already handles it.
- **No Stripe wiring here** — plan buttons still point to `/download/arm64`. Real checkout is
  a separate task needing keys.
- Keep the `pv` toggle numbers (`data-a`) as the discounted monthly figure ($4/$8/$16); the
  billed-per-year note ($48/$96/$192) already lives in the `#b1/#b2/#b3` spans.
