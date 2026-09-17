"""How a visitor reaches a human: the WhatsApp buttons and the social links.

The page has promised "WhatsApp support" in three places since it was written,
and carried no number anywhere — the footer's WhatsApp link pointed at ``#``.
The four social icons pointed at ``#`` too. An icon that does nothing when you
click it is worse than no icon at all on a page asking AED 350 from a brand the
visitor has never heard of: it reads as abandoned.

So neither is written into the HTML any more. Both come from settings, and
anything unset renders as nothing at all rather than as a link to nowhere. The
number is not in the repo because it is an operational detail, not code — it
changes when the SIM does, and a repo is the wrong place to learn it from.

Set ``WHATSAPP_NUMBER`` (digits, with country code) and every WhatsApp button
on the page appears. Set ``SOCIAL_INSTAGRAM`` and that one icon appears. Set
nothing and the page is simply quieter, which is the honest state.

Each button carries its own pre-filled message, so an enquiry arrives already
saying where it came from — "the L802 card" is a different lead from "the
pricing table", and knowing which is free here and impossible later.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import quote

from app.config import Settings

# A WhatsApp number is only ever digits with a country code; people write them
# with +, spaces, dashes and brackets, and wa.me accepts none of that.
_DIGITS = re.compile(r"\D+")

# Country codes run 1–3 digits and subscriber numbers 4–12, so anything outside
# this is a typo (a local number with the country code forgotten, usually) and
# would produce a wa.me link that silently opens a chat with nobody.
_MIN_DIGITS, _MAX_DIGITS = 8, 15

# Icons are drawn, not typed: the page used 📷 and ▶ for Instagram and YouTube,
# which render as a camera and a triangle on half the devices that see them.
# Paths are the brands' own marks, used to link to that brand's page.
_SOCIALS: tuple[tuple[str, str, str], ...] = (
    ("instagram", "Instagram", "M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98C.014 8.332 0 8.741 0 12s.014 3.668.072 4.948c.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24s3.668-.014 4.948-.072c4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"),
    ("linkedin", "LinkedIn", "M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"),
    ("youtube", "YouTube", "M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"),
    ("facebook", "Facebook", "M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"),
    ("x", "X", "M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932ZM17.61 20.644h2.039L6.486 3.24H4.298Z"),
)

# The official WhatsApp mark, for a button that opens WhatsApp.
_WA_PATH = "M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z"

# Where each button sits -> what the chat already says when it opens. Written as
# the visitor, because that is who appears to be sending it.
MESSAGES: dict[str, str] = {
    "fab": "Hi FarryOn! I have a question about your AI smart glasses.",
    "l801": "Hi FarryOn! I'd like to know more about the L801 Business glasses.",
    "l802": "Hi FarryOn! I'd like to know more about the L802 Premium glasses.",
    "gs4": "Hi FarryOn! I'd like to know more about the Farry-GS4 glasses.",
    "gs5": "Hi FarryOn! I'd like to know more about the GS5 MAX glasses.",
    "cta": "Hi FarryOn! I have a question before I order.",
    "footer": "Hi FarryOn! I need some help.",
    "about": "Hi FarryOn! I read your About page and have a question.",
}


def number(settings: Settings) -> str | None:
    """The WhatsApp number as wa.me wants it, or None if unusable.

    A number that cannot work is treated as absent rather than rendered: a
    button that opens a chat with nobody costs more than a missing button.
    """
    raw = getattr(settings, "whatsapp_number", None)
    if not raw:
        return None
    digits = _DIGITS.sub("", str(raw))
    if not _MIN_DIGITS <= len(digits) <= _MAX_DIGITS:
        return None
    return digits


def link(settings: Settings, where: str) -> str | None:
    """The wa.me URL for one button, pre-filled with that button's message."""
    digits = number(settings)
    if not digits:
        return None
    text = MESSAGES.get(where, MESSAGES["fab"])
    return f"https://wa.me/{digits}?text={quote(text)}"


def _safe_url(value: object) -> str | None:
    """Only ever emit an http(s) link.

    These come from the environment, and an environment variable ends up inside
    an href — `javascript:` in one would be a script running on the page.
    """
    if not value:
        return None
    url = str(value).strip()
    if not url.lower().startswith(("https://", "http://")):
        return None
    return url


# A network whose page is not up yet: the icon shows, greyed, with "Soon" —
# because a footer with one lonely Instagram icon reads as a brand with no
# presence, while a hidden network is a promise nobody can see.
_SOON = frozenset({"soon", "coming soon", "coming-soon"})


def _is_soon(value: object) -> bool:
    return bool(value) and str(value).strip().lower() in _SOON


def social_html(settings: Settings) -> str:
    """The footer's social icons — the networks with a URL set, plus any
    marked ``soon`` (shown greyed, not linked)."""
    out = []
    for key, label, path in _SOCIALS:
        raw = getattr(settings, f"social_{key}", None)
        svg = (
            f'<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor" '
            f'aria-hidden="true"><path d="{path}"/></svg>'
        )
        if _is_soon(raw):
            out.append(
                f'<span class="s-link s-soon" title="{label} — coming soon" '
                f'aria-label="{label}: coming soon">{svg}<em>Soon</em></span>'
            )
            continue
        url = _safe_url(raw)
        if not url:
            continue
        out.append(
            f'<a class="s-link" href="{escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer" aria-label="FarryOn on {label}">'
            f'<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor" '
            f'aria-hidden="true"><path d="{path}"/></svg></a>'
        )
    return "".join(out)


def _icon(size: int) -> str:
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" '
        f'fill="currentColor" aria-hidden="true"><path d="{_WA_PATH}"/></svg>'
    )


# The icons are <svg fill="currentColor"> inside a bare <a>, and a bare <a>
# inherits the browser's own link colour — which rendered Instagram blue and,
# once clicked, YouTube visited-purple. The footer's other links get their
# colour from `.footer-col a`, which these are not inside.
_SOCIAL_CSS = """
<style>
.footer-social .s-link{color:var(--tm);transition:color .25s ease,
  border-color .3s ease,background .3s ease,box-shadow .3s ease}
.footer-social .s-link:hover{color:var(--pl)}
.footer-social .s-link:focus-visible{outline:2px solid var(--pl);
  outline-offset:2px;color:var(--pl)}
/* A network that is not live yet: greyed, unclickable, labelled. */
.footer-social .s-soon{position:relative;opacity:.45;cursor:default}
.footer-social .s-soon:hover{color:var(--tm)}
.footer-social .s-soon em{position:absolute;left:50%;top:100%;transform:translateX(-50%);
  margin-top:3px;font-size:.58rem;font-style:normal;letter-spacing:.08em;
  text-transform:uppercase;color:var(--tm);white-space:nowrap}
</style>
"""

_WA_CSS = """
<style>
/* ═══════════════════════════════════════════
   WHATSAPP — floating button + inline buttons
═══════════════════════════════════════════ */
.wa-fab{position:fixed;right:22px;bottom:22px;z-index:900;
  width:56px;height:56px;border-radius:50%;background:#25D366;color:#fff;
  display:flex;align-items:center;justify-content:center;text-decoration:none;
  box-shadow:0 6px 24px rgba(37,211,102,.38);
  transition:transform .25s ease,box-shadow .25s ease,opacity .25s ease}
.wa-fab:hover{transform:scale(1.07);box-shadow:0 8px 30px rgba(37,211,102,.55)}
.wa-fab:focus-visible{outline:3px solid #fff;outline-offset:3px}
/* The closing banner has a WhatsApp button of its own; two of them on screen
   at once is one too many, and on a phone the floating one sits on top of the
   banner's call to action. */
.wa-fab.is-tucked{opacity:0;transform:scale(.6);pointer-events:none}
.wa-fab::after{content:'';position:absolute;inset:-6px;border-radius:50%;
  border:2px solid rgba(37,211,102,.5);animation:waPulse 2.6s ease-out infinite}
@keyframes waPulse{
  0%{transform:scale(.85);opacity:.7}
  70%{transform:scale(1.25);opacity:0}
  100%{opacity:0}
}

/* Inline buttons: on the spec cards, in the closing banner, in the footer. */
.wa-btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;
  background:rgba(37,211,102,.09);border:1px solid rgba(37,211,102,.34);
  color:#5BE58C;text-decoration:none;border-radius:11px;
  padding:12px 18px;font-size:.84rem;font-weight:600;font-family:var(--fb);
  transition:background .2s ease,border-color .2s ease,color .2s ease}
.wa-btn:hover{background:rgba(37,211,102,.17);border-color:rgba(37,211,102,.6);
  color:#8BF0AE}
.wa-btn:focus-visible{outline:2px solid #25D366;outline-offset:2px}
.wa-btn svg{flex:0 0 auto}
/* On a spec card it is the last thing in the card and spans it. */
.wa-card{margin:4px 26px 22px;display:flex}
.wa-card .wa-btn{flex:1}
/* In the closing banner it sits under the primary button, quieter than it. */
.wa-cta{margin-top:18px;display:flex;justify-content:center}
.wa-hours{margin-top:10px;font-size:.76rem;color:var(--td)}
/* In the footer list it is a plain link like its neighbours, just green. */
.footer-col a.wa-inline{color:#5BE58C;display:inline-flex;align-items:center;gap:7px}
.footer-col a.wa-inline:hover{color:#8BF0AE}

@media(max-width:600px){
  .wa-fab{right:16px;bottom:16px;width:52px;height:52px}
}
@media(prefers-reduced-motion:reduce){
  .wa-fab,.wa-fab:hover{transition:none;transform:none}
  .wa-fab::after{animation:none;opacity:0}
}
</style>
"""

_JS = """
<script>
/* The floating button steps aside while the closing banner — which has its own
   WhatsApp button — is on screen. */
(function(){
  var fab=document.querySelector('.wa-fab');
  var banner=document.querySelector('.cta-banner');
  if(!fab||!banner||!('IntersectionObserver' in window)) return;
  new IntersectionObserver(function(entries){
    fab.classList.toggle('is-tucked',entries[0].isIntersecting);
  },{threshold:.28}).observe(banner);
})();
</script>
"""


def _fab(settings: Settings) -> str:
    url = link(settings, "fab")
    if not url:
        return ""
    return (
        f'<a class="wa-fab" href="{escape(url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer" aria-label="Chat with FarryOn on WhatsApp">'
        f"{_icon(29)}</a>"
    )


def _button(settings: Settings, where: str, label: str, classes: str = "") -> str:
    url = link(settings, where)
    if not url:
        return ""
    extra = f" {classes}" if classes else ""
    return (
        f'<a class="wa-btn{extra}" href="{escape(url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{_icon(17)}<span>{escape(label)}</span></a>'
    )


def _card_button(settings: Settings, slug: str, model: str) -> str:
    button = _button(settings, slug, f"Ask about the {model}")
    return f'<div class="wa-card">{button}</div>' if button else ""


def _cta_block(
    settings: Settings, where: str = "cta", label: str = "Questions? Chat on WhatsApp"
) -> str:
    button = _button(settings, where, label)
    if not button:
        return ""
    hours = getattr(settings, "whatsapp_hours", None)
    # Saying when someone answers is the difference between a quiet reply and a
    # visitor deciding they have been ignored.
    note = (
        f'<p class="wa-hours">{escape(str(hours))}</p>'
        if hours and str(hours).strip()
        else ""
    )
    return f'<div class="wa-cta">{button}</div>{note}'


def _footer_link(settings: Settings) -> str:
    url = link(settings, "footer")
    if not url:
        return ""
    return (
        f'<li><a class="wa-inline" href="{escape(url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{_icon(13)}<span>WhatsApp</span></a></li>'
    )


def render(html: str, settings: Settings) -> str:
    """Fill the contact placeholders, leaving nothing behind where unset."""
    from app.web.products import MODELS

    fab = _fab(settings)
    html = html.replace("<!--WHATSAPP_FAB-->", fab)
    html = html.replace("<!--WHATSAPP_CTA-->", _cta_block(settings))
    # The About page's closing block: same button and hours, its own message.
    html = html.replace(
        "<!--WHATSAPP_ABOUT-->", _cta_block(settings, "about", "Ask us anything on WhatsApp")
    )
    html = html.replace("<!--WHATSAPP_FOOTER-->", _footer_link(settings))
    for slug, model in MODELS.items():
        html = html.replace(
            f"<!--WHATSAPP_CARD:{slug}-->", _card_button(settings, slug, model)
        )
    html = html.replace("<!--SOCIAL_LINKS-->", social_html(settings))
    # Each stylesheet ships only if something on the page needs it, and the
    # two are independent: socials can be set without a WhatsApp number, and
    # the other way round.
    styles = (_WA_CSS if number(settings) else "") + (
        _SOCIAL_CSS if social_html(settings) else ""
    )
    script = _JS if fab else ""
    return html.replace("<!--CONTACT_CSS-->", styles).replace(
        "<!--CONTACT_JS-->", script
    )
