"""The two order mails, in the FarryOn look: the customer's confirmation and
the operator's "a parcel to pack".

One HTML shell (dark navy, teal accents, the logo from the public site) and
a plain-text twin for clients that show no HTML. Everything the customer
typed is escaped; nothing here is built from the request, only from the
order row the webhook wrote.
"""

from __future__ import annotations

from html import escape

from app.config import get_settings
from app.db.models import Order
from app.logging_conf import get_logger

logger = get_logger(__name__)

# Brand palette — the website's (Midnight Aurora): base, card, teal, text.
_BG = "#030912"
_CARD = "#0b1526"
_LINE = "rgba(0,212,170,0.16)"
_TEAL = "#00D4AA"
_TEAL_DEEP = "#0F6E56"
_TEXT = "#e7ecf3"
_MUTED = "#93a1b5"


def _site() -> str:
    return get_settings().sso_redirect_base_url.rstrip("/")


def money(minor: int, currency: str) -> str:
    """"AED 450", "₹11,999", "$129.00" — the way each currency is read."""
    c = (currency or "AED").upper()
    if c == "INR":
        return f"₹{round(minor / 100):,}"
    if c == "USD":
        return f"${minor / 100:,.2f}"
    return f"{c} {minor / 100:,.0f}" if minor % 100 == 0 else f"{c} {minor / 100:,.2f}"


def address_line(a: dict) -> str:
    parts = [
        a.get("line1"), a.get("line2"),
        f"PO Box {a['po_box']}" if a.get("po_box") else None,
        a.get("city"), a.get("state"), a.get("postal_code"), a.get("country"),
    ]
    return ", ".join(str(p) for p in parts if p)


def _line_total(i: dict) -> int:
    return int(i.get("price") or i.get("price_aed") or 0) * int(i.get("qty") or 0) * 100


def _item_name(i: dict) -> str:
    return f"{i.get('qty')} × {i.get('name')}" + (f" ({i['colour']})" if i.get("colour") else "")


def _shell(*, title: str, lead: str, body: str, cta_text: str | None, cta_link: str | None, foot: str) -> str:
    site = _site()
    cta = (
        f'<tr><td align="center" style="padding:6px 0 22px">'
        f'<a href="{escape(cta_link, quote=True)}" style="display:inline-block;background:linear-gradient(135deg,{_TEAL_DEEP},{_TEAL});'
        f'color:{_BG};text-decoration:none;padding:13px 30px;border-radius:50px;font-weight:700;font-size:14px">'
        f"{escape(cta_text)}</a></td></tr>"
        if cta_text and cta_link
        else ""
    )
    return f"""\
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:{_BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:28px 12px">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;font-family:Inter,Arial,Helvetica,sans-serif;color:{_TEXT}">
  <tr><td align="center" style="padding:0 0 18px">
    <img src="{site}/farry-icon.png" width="44" height="44" alt="FarryOn" style="display:inline-block;vertical-align:middle;border-radius:12px">
    <span style="display:inline-block;vertical-align:middle;margin-left:10px;font-size:20px;font-weight:700;letter-spacing:.2px">Farry<span style="color:{_TEAL}">On</span></span>
  </td></tr>
  <tr><td style="background:{_CARD};border:1px solid {_LINE};border-radius:18px;padding:30px 28px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr><td style="font-size:22px;font-weight:700;padding-bottom:6px">{escape(title)}</td></tr>
      <tr><td style="font-size:14px;line-height:1.6;color:{_MUTED};padding-bottom:18px">{lead}</td></tr>
      {body}
      {cta}
      <tr><td style="font-size:12px;line-height:1.6;color:{_MUTED};border-top:1px solid {_LINE};padding-top:16px">{foot}</td></tr>
    </table>
  </td></tr>
  <tr><td align="center" style="font-size:11px;color:{_MUTED};padding:18px 0 0">FarryOn · AI smart glasses · <a href="{site}" style="color:{_TEAL};text-decoration:none">{escape(site.replace('https://', ''))}</a></td></tr>
</table>
</td></tr></table></body></html>"""


def _items_table(items: list[dict], currency: str, delivery_minor: int, total_minor: int) -> str:
    rows = "".join(
        f'<tr><td style="padding:9px 0;border-bottom:1px solid {_LINE};font-size:14px">{escape(_item_name(i))}</td>'
        f'<td align="right" style="padding:9px 0;border-bottom:1px solid {_LINE};font-size:14px;white-space:nowrap">{escape(money(_line_total(i), currency))}</td></tr>'
        for i in items
    )
    rows += (
        f'<tr><td style="padding:9px 0;font-size:14px;color:{_MUTED}">Delivery</td>'
        f'<td align="right" style="padding:9px 0;font-size:14px;color:{_MUTED}">{escape(money(delivery_minor, currency)) if delivery_minor else "Free"}</td></tr>'
        f'<tr><td style="padding:12px 0 0;font-size:16px;font-weight:700;border-top:1px solid {_LINE}">Total paid</td>'
        f'<td align="right" style="padding:12px 0 0;font-size:16px;font-weight:700;border-top:1px solid {_LINE};color:{_TEAL}">{escape(money(total_minor, currency))}</td></tr>'
    )
    return f'<tr><td style="padding-bottom:18px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'


def _facts(rows: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<tr><td style="padding:5px 0;font-size:13px;color:{_MUTED};width:38%;vertical-align:top">{escape(k)}</td>'
        f'<td style="padding:5px 0;font-size:13px;vertical-align:top">{escape(v)}</td></tr>'
        for k, v in rows
        if v
    )
    return f'<tr><td style="padding-bottom:18px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:rgba(0,212,170,0.04);border:1px solid {_LINE};border-radius:12px;padding:12px 14px">{cells}</table></td></tr>'


def customer_confirmation(order: Order, items: list[dict], address: dict, delivery_minor: int) -> tuple[str, str, str]:
    """(subject, text, html) for the buyer."""
    cur = order.currency
    total = money(order.amount_cents, cur)
    where = address_line(address)
    first = (order.name or "").split(" ")[0]
    subject = f"Your FarryOn order #{order.id} is confirmed"
    text = "\n".join(
        [
            f"Hi {first}," if first else "Hi,",
            "",
            f"Thank you for your order. Order #{order.id} is confirmed and being prepared.",
            "",
            *[f"  {_item_name(i)} — {money(_line_total(i), cur)}" for i in items],
            f"  Delivery — {money(delivery_minor, cur) if delivery_minor else 'Free'}",
            f"  Total paid — {total}",
            "",
            f"Delivering to: {where}",
            f"Contact: {order.phone or '-'}",
            "",
            "We will message you on this email and your phone as soon as your glasses ship.",
            "Questions? Reply to this email or reach us on WhatsApp.",
            "",
            "— The FarryOn team",
        ]
    )
    body = _items_table(items, cur, delivery_minor, order.amount_cents) + _facts(
        [("Delivering to", where), ("Phone", order.phone or ""), ("Order number", f"#{order.id}")]
    )
    html = _shell(
        title="Order confirmed ✓",
        lead=(f"Hi {escape(first)}, thank you for your order." if first else "Thank you for your order.")
        + f" <b style='color:{_TEXT}'>Order #{order.id}</b> is confirmed and being prepared. We will message you on this email and your phone as soon as your glasses ship.",
        body=body,
        cta_text="Visit FarryOn",
        cta_link=_site() + "/#glasses",
        foot="Questions about your order? Reply to this email or reach us on WhatsApp. Keep this email as your receipt.",
    )
    return subject, text, html


def operator_alert(order: Order, items: list[dict], address: dict, delivery_minor: int) -> tuple[str, str, str]:
    """(subject, text, html) for whoever packs the parcel."""
    cur = order.currency
    total = money(order.amount_cents, cur)
    where = address_line(address)
    subject = f"New order #{order.id} — {total} — {address.get('country') or '?'}"
    text = "\n".join(
        [
            f"New glasses order #{order.id} — {total}",
            "",
            *[f"  {_item_name(i)} — {money(_line_total(i), cur)}" for i in items],
            f"  Delivery — {money(delivery_minor, cur) if delivery_minor else 'Free'}",
            "",
            f"Customer: {order.name or '-'}",
            f"Email: {order.email or '-'}",
            f"Phone: {order.phone or '-'}",
            f"Ship to: {where}",
            "",
            f"Stripe session: {order.session_id}",
            f"Mark it shipped: {_site()}/admin/orders",
        ]
    )
    body = _items_table(items, cur, delivery_minor, order.amount_cents) + _facts(
        [
            ("Customer", order.name or "-"),
            ("Email", order.email or "-"),
            ("Phone", order.phone or "-"),
            ("Ship to", where),
            ("Stripe session", order.session_id),
        ]
    )
    html = _shell(
        title=f"New order #{order.id}",
        lead=f"A customer just paid <b style='color:{_TEXT}'>{escape(total)}</b> for glasses. Pack the parcel below and mark it shipped in the admin panel.",
        body=body,
        cta_text="Open in admin panel",
        cta_link=_site() + "/admin/orders",
        foot="Sent by the FarryOn shop the moment Stripe confirmed the payment.",
    )
    return subject, text, html


# What each status change says. The customer's line is the message; the
# operator's is the record.
_STATUS_COPY = {
    "shipped": (
        "Your glasses are on the way 🚚",
        "Good news — order #{id} has shipped and is on its way to you.",
        "We will share tracking details on WhatsApp or by email if the courier provides them.",
    ),
    "delivered": (
        "Your glasses have arrived ✓",
        "Order #{id} has been delivered. We hope you love them.",
        "Open the FarryOn app, pair your glasses, and start talking to Farry. Questions? Reply to this email.",
    ),
    "cancelled": (
        "Order cancelled",
        "Order #{id} has been cancelled.",
        "If you paid and have not been refunded yet, reply to this email and we will sort it out straight away.",
    ),
    "paid": (
        "Order confirmed ✓",
        "Order #{id} is confirmed and being prepared.",
        "We will message you as soon as your glasses ship.",
    ),
}


def customer_status(order: Order, items: list[dict], address: dict, delivery_minor: int, note: str | None) -> tuple[str, str, str]:
    """(subject, text, html) telling the buyer the order moved to its status."""
    title, lead, foot = _STATUS_COPY.get(order.status, _STATUS_COPY["paid"])
    lead = lead.format(id=order.id)
    cur = order.currency
    first = (order.name or "").split(" ")[0]
    where = address_line(address)
    note_line = f"Note from FarryOn: {note}" if note else ""
    subject = f"FarryOn order #{order.id}: {title.rstrip(' 🚚✓')}"
    text = "\n".join(
        [
            f"Hi {first}," if first else "Hi,",
            "",
            lead,
            *([note_line, ""] if note_line else [""]),
            *[f"  {_item_name(i)} — {money(_line_total(i), cur)}" for i in items],
            f"  Delivery — {money(delivery_minor, cur) if delivery_minor else 'Free'}",
            f"  Total — {money(order.amount_cents, cur)}",
            "",
            f"Delivering to: {where}",
            "",
            foot,
            "",
            "— The FarryOn team",
        ]
    )
    facts = [("Delivering to", where), ("Order number", f"#{order.id}"), ("Status", order.status)]
    if note:
        facts.append(("Note", note))
    body = _items_table(items, cur, delivery_minor, order.amount_cents) + _facts(facts)
    html = _shell(
        title=title,
        lead=(f"Hi {escape(first)}, " if first else "") + escape(lead),
        body=body,
        cta_text="Visit FarryOn",
        cta_link=_site() + "/#glasses",
        foot=escape(foot),
    )
    return subject, text, html


def operator_status(order: Order, items: list[dict], address: dict, delivery_minor: int, note: str | None, by: str | None) -> tuple[str, str, str]:
    """(subject, text, html) — the record of who moved the order and to what."""
    cur = order.currency
    where = address_line(address)
    who = by or "an admin"
    subject = f"Order #{order.id} marked {order.status} — {money(order.amount_cents, cur)}"
    text = "\n".join(
        [
            f"Order #{order.id} is now {order.status.upper()} (by {who}).",
            *([f"Note: {note}"] if note else []),
            "",
            *[f"  {_item_name(i)} — {money(_line_total(i), cur)}" for i in items],
            f"  Delivery — {money(delivery_minor, cur) if delivery_minor else 'Free'}",
            "",
            f"Customer: {order.name or '-'} · {order.email or '-'} · {order.phone or '-'}",
            f"Ship to: {where}",
            "",
            f"{_site()}/admin/orders",
        ]
    )
    facts = [("Status", order.status), ("Changed by", who), ("Customer", order.name or "-"), ("Email", order.email or "-"), ("Phone", order.phone or "-"), ("Ship to", where)]
    if note:
        facts.append(("Note", note))
    body = _items_table(items, cur, delivery_minor, order.amount_cents) + _facts(facts)
    html = _shell(
        title=f"Order #{order.id} · {order.status}",
        lead=f"Marked <b style='color:{_TEXT}'>{escape(order.status)}</b> by {escape(who)}. The customer has been emailed.",
        body=body,
        cta_text="Open in admin panel",
        cta_link=_site() + "/admin/orders",
        foot="Sent by the FarryOn shop when an order's status changes in the admin panel.",
    )
    return subject, text, html
