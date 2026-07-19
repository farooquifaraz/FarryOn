# Stripe setup (Phase 2 — checkout)

The code is done and tested against a mocked Stripe. To make it actually charge,
you fill in four values from your Stripe dashboard. **Use Test mode first**
(toggle top-right of the dashboard) — test keys start `sk_test_…` and take fake
cards like `4242 4242 4242 4242`.

## 1. Create the two products/prices

Stripe is the source of truth for the amount actually charged. Create one Price
per plan:

Dashboard → **Product catalog → Add product**, twice:

| Product name | Price | Billing period | → gives you |
|---|---|---|---|
| FarryOn Plus | **$9.99** | Monthly (recurring) | a Price id `price_…` |
| FarryOn Pro  | **$19.99** | Monthly (recurring) | a Price id `price_…` |

Copy each **Price id** (starts `price_`, on the price row — NOT the product id
`prod_`).

## 2. Get your secret key

Dashboard → **Developers → API keys → Secret key** (`sk_test_…`). Reveal + copy.

## 3. Put them in the backend environment

In `backend/.env` (create the lines if absent):

```
STRIPE_SECRET_KEY=sk_test_YOURKEY
STRIPE_PRICE_IDS={"plus":"price_YOURPLUS","pro":"price_YOURPRO"}
STRIPE_SUCCESS_URL=https://farryon.app/billing/success?session_id={CHECKOUT_SESSION_ID}
STRIPE_CANCEL_URL=https://farryon.app/billing/cancel
```

- `STRIPE_PRICE_IDS` is JSON on one line — the keys (`plus`/`pro`) are OUR plan
  names, the values are Stripe's price ids. They must match the plan names in
  `app/db/seed.py` `PLAN_CATALOG`.
- The success/cancel URLs are where Stripe returns the user. For now they can
  point anywhere; when the mobile flow is wired we'll point them at the app's
  deep links. `{CHECKOUT_SESSION_ID}` is filled in by Stripe.

**Never commit these** — `.env` is gitignored. Give me the key and price ids by
pasting them into `.env` yourself; I don't need to see the secret key.

## 4. Try it

With the backend running and a signed-in user's token:

```
POST /api/v1/billing/checkout   { "plan": "pro" }
→ { "success": true, "data": { "url": "https://checkout.stripe.com/c/pay/cs_test_…" } }
```

Open that URL, pay with `4242 4242 4242 4242` (any future expiry, any CVC). The
payment succeeds in Stripe — but **the subscription won't flip active in our DB
until Phase 3** (the webhook that hears "payment completed" and writes the row).
That's the next piece.

## 5. Wire the webhook (Phase 3 — now built)

The webhook is what turns a completed payment into an active subscription in our
DB. Without it, checkout charges the card but the app never learns the user
subscribed.

**In the Stripe dashboard:** Developers → Webhooks → **Add endpoint**.

- Endpoint URL: `https://YOUR_BACKEND/api/v1/webhooks/stripe`
  (for local testing, use the Stripe CLI instead — see below)
- Events to send — select these five:
  - `checkout.session.completed`  ← the activation event
  - `customer.subscription.deleted`
  - `customer.subscription.updated`
  - `invoice.payment_succeeded`
  - `invoice.payment_failed`
- After creating it, copy the **Signing secret** (`whsec_…`).

Add it to `backend/.env`:

```
STRIPE_WEBHOOK_SECRET=whsec_YOURSECRET
```

**Local testing without a public URL** — the Stripe CLI forwards events to your
localhost and prints a `whsec_…` to use:

```
stripe login
stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
# → prints "Ready! Your webhook signing secret is whsec_…"  — put that in .env
stripe trigger checkout.session.completed
```

### The end-to-end flow, once both secrets are set

1. App calls `POST /billing/checkout {plan}` → gets a Stripe URL.
2. User pays with `4242 4242 4242 4242`.
3. Stripe fires `checkout.session.completed` → our webhook verifies the
   signature, reads `metadata.user_id` + `metadata.plan`, and creates an
   **active** subscription row (idempotent — a redelivery won't double it).
4. `billing.active_plan_name` now returns that plan, so the user's quota caps
   are the plan's caps. Renewals (`invoice.payment_succeeded`) and cancellations
   flow through the same webhook.

## What's still NOT done (Phase 4)

- Turning on **quota enforcement** (`quota_enforcement_enabled=true`) so the
  caps actually apply. Deliberately last — after the money path is proven
  end-to-end with real test payments.
- The **mobile upgrade flow**: a "you've hit today's limit — upgrade" prompt
  that opens the checkout URL, and handling the success/cancel return.
