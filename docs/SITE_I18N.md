# The site in more than one language

English is the source. `backend/app/web/index.html` and `about.html` are
written in English, and every module that fills them (pricing, contact,
products, rates) renders English. Other languages are dictionaries.

| Language | URL | Dictionary |
| --- | --- | --- |
| English | `/`, `/about` | — (the HTML itself) |
| Hindi | `/hi`, `/hi/about` | `backend/app/web/i18n/hi.json` |

Arabic is phase 2: a `ar.json` plus RTL styling (`dir="rtl"` and a mirrored
layout pass) — the machinery below does not need to change for it.

## How it works (`backend/app/web/i18n.py`)

The finished English page is translated as the **last** render step, text
node by text node, so the dynamic parts (plan cards, WhatsApp buttons, the
catalog link, the currency picker) are translated exactly like the static
ones. Nothing in the HTML is marked up for it.

- A dictionary key is the **exact English text** as it appears on the page
  (whitespace collapsed, entities as in the HTML, e.g. `See &amp; Ask`).
- `_regex` rules handle text with numbers or names in it:
  `"200 talk minutes a month"`, `"Ask about the GS5 MAX"`, `"Save ₹288"`.
- `_head` holds the `<title>` and meta descriptions.
- A few attributes are translated too: `alt`, `aria-label`, `title`,
  `placeholder`, and `data-m`/`data-a` (the "per month"/"per year" words the
  annual toggle swaps in).
- **A string with no translation stays English** — never blank, never a key.
  Product and brand names (FarryOn, L801, GS5 MAX, Sony 8MP) are left out on
  purpose.
- Internal links on a Hindi page stay in Hindi (`/about` → `/hi/about`,
  `/#pricing` → `/hi#pricing`); downloads, `/privacy`, `/terms`, `/media`
  are shared.
- Every page carries `hreflang` links to its siblings and `x-default`, which
  is what makes a Hindi search return the Hindi page. Caddy forwards
  `/hi` and `/hi/*` (`admin/Caddyfile`).
- The nav has an **EN | हिंदी** switch (plain links). A first-time visitor
  whose browser language is Hindi is sent from `/` to `/hi` once; after that
  their own choice (`localStorage` `site-lang`) wins. Crawlers are never
  redirected.
- Style, per Faraz (2026-09-18): natural Hindi with everyday English tech
  words — स्मार्ट ग्लासेस, ऐप, AI असिस्टेंट — the way Indian brands write.

## Editing

**Changing English copy:** edit the HTML as always. Then run

```bash
cd backend && python -m pytest tests/test_site_i18n.py -q
```

`test_every_hindi_key_is_still_on_the_page_or_a_rule` fails naming the
Hindi keys whose English no longer exists — update those keys in `hi.json`
(the old English text is the key; replace it with the new text and refresh
the Hindi). Until you do, that text shows in English on `/hi`.

**Adding copy:** write it in English in the HTML, add the same text as a key
in `hi.json` with its Hindi. To see what is still English on the Hindi page:

```bash
cd backend && python - <<'EOF'
import re
from app.config import get_settings
from app.web import pricing, contact, products, i18n, router as web
s = get_settings()
page = contact.render(products.render(pricing.render(web._INDEX.read_text(encoding="utf-8"), s), s), s)
hi = i18n.localize(page, "hi", path_en="/", site="https://x")
body = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", "", hi[hi.index("<body"):], flags=re.S)
for t in sorted({re.sub(r"\s+"," ",t).strip() for t in re.findall(r">([^<>]+)<", body)}):
    if re.search(r"[A-Za-z]{3,}", t) and not re.search(r"[ऀ-ॿ]", t): print(t)
EOF
```

**Adding a language:** add it to `i18n.LANGUAGES` (code, native name, path
prefix), create `i18n/<code>.json`, add the two routes in `web/router.py`
(copy the `/hi` pair) and the prefix to Caddy's forward list.
