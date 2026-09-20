# The glasses shop

Glasses are sold from the landing page: **Buy now** / **Add to cart** on each
spec card, a cart drawer (the 🛒 button in the nav, kept in the browser's
localStorage), and **Checkout**, which opens a Stripe Checkout page.

## How the money moves

1. The cart's second step asks for the buyer's details on our page: name,
   email, phone, address lines, PO Box, city, state/emirate, country (only
   the countries in `SHOP_SHIP_COUNTRIES`, default `AE`). Validated in the
   browser and again by the API.
2. `POST /api/v1/shop/checkout` with the cart (`[{slug, qty, colour}]`) and
   the `customer`. No account needed. The backend prices every line from
   `backend/app/web/products.py` → `PRICES_AED` (the client never sends a
   price), puts the buyer's details in the session's metadata, and builds a
   one-payment Checkout Session in AED — Stripe only takes the card, with
   the email prefilled. Answers `{url}`.
3. Success sends the visitor back to `/?order=<session id>#glasses`: the
   landing page fetches `GET /api/v1/shop/orders/<id>` and shows the order
   in a modal (items, total, address, email). Cancel returns to
   `/?cart=cancelled#glasses`, which reopens the cart with its contents.
4. Stripe's `checkout.session.completed` webhook arrives with
   `metadata.kind = glasses_order`. `modules/billing/router.py` hands it to
   `modules/shop/service.record_order`, which writes ONE `orders` row (keyed
   on the session id — redeliveries are no-ops), mails the buyer a
   confirmation, and mails `SHOP_NOTIFY_EMAIL` (falls back to
   `FIRST_SUPER_ADMIN_EMAIL`) what to ship where.
5. Admin panel → **Orders**: customer, phone, address, items, total; move the
   order paid → shipped → delivered (or cancelled), with a note.

Nothing is written when a checkout starts; an abandoned cart leaves no row.

## Sold out

Admin panel → **Orders** → the **Stock** card at the top: click a model's
"On sale" button to mark it out of stock, or a colour chip to take just
that colour off sale; click again to put it back. It takes effect on the
next page load (no restart): the model's card shows one disabled "Out of
stock" button, the colour is greyed out in the picker, a cart that still
holds it cannot check out, and the API refuses it (`OUT_OF_STOCK`).

`SHOP_OUT_OF_STOCK=l802,gs5:Red` in `.env` does the same from the server
side (needs a restart); the two lists are unioned.

## Changing a price or adding a colour

Edit `PRICES_AED` / `COLOURS` in `backend/app/web/products.py`. The card, the
cart total, the charge and the tests all read from there.

## Testing locally

The local backend uses the test-mode Stripe key. Stripe cannot reach a
laptop, so the order row only appears if the webhook is forwarded:

```
stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
```

(its `whsec_…` must be `STRIPE_WEBHOOK_SECRET` in `backend/.env`). Pay with
`4242 4242 4242 4242`, any future date, any CVC.

## Not yet

Shipping fees (none charged), stock, refunds from the admin panel, customer
order history. The Stripe dashboard shows every payment with its address.
