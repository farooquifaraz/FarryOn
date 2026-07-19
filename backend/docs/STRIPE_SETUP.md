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

## What's NOT done yet (Phase 3)

- The Stripe **webhook** — Stripe calls us on `checkout.session.completed` /
  `customer.subscription.*` and we write/activate the subscription. Until then,
  checkout works and charges, but the app won't know the user is subscribed.
- Turning on **quota enforcement** (`quota_enforcement_enabled=true`) so the
  plan's caps actually apply. That's deliberately last, after the money path is
  proven end-to-end.
