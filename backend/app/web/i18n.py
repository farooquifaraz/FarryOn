"""The public site in more than one language.

English is the source: ``index.html`` and ``about.html`` are written in it,
and every other module (pricing, contact, products) renders English. A
translation is a JSON file per language under ``web/i18n/`` whose keys are
the exact English text as it appears on the rendered page — so nothing in
the HTML had to be marked up, no template language was introduced, and a
designer editing the English page cannot break the Hindi one: a string
without a translation simply stays English (never blank, never a key).

The page is translated as the LAST render step, text node by text node
(and a few attributes), so the dynamic parts get the same treatment as the
static ones. Each language lives at its own path (``/hi``, ``/hi/about``)
with ``hreflang`` links between them, which is what search engines need to
show a Hindi result to a Hindi search; a script-only toggle would not.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_DIR = _HERE / "i18n"

#: Public languages: code -> (native name, path prefix). English is the root.
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", ""),
    "hi": ("हिंदी", "/hi"),
}
DEFAULT_LANG = "en"

#: Attributes whose values are visible or spoken and therefore translated.
#: ``data-m``/``data-a`` carry the "per month"/"per year" words the annual
#: toggle swaps in; ``alt``/``aria-label`` are what a screen reader says.
_ATTRS = ("alt", "aria-label", "title", "placeholder", "data-m", "data-a")

_WS = re.compile(r"\s+")


class Translation:
    """One language's dictionary: exact strings, regex rules, head strings."""

    def __init__(self, lang: str, raw: dict[str, Any]) -> None:
        self.lang = lang
        self.exact: dict[str, str] = {
            k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)
        }
        self.head: dict[str, str] = dict(raw.get("_head", {}))
        self.rules: list[tuple[re.Pattern[str], str]] = [
            (re.compile(pattern), repl) for pattern, repl in raw.get("_regex", [])
        ]

    def lookup(self, text: str) -> str | None:
        """The translation of one normalised text node, or None."""
        hit = self.exact.get(text)
        if hit is not None:
            return hit
        for pattern, repl in self.rules:
            if pattern.search(text):
                return pattern.sub(repl, text)
        return None


@lru_cache(maxsize=8)
def load(lang: str) -> Translation | None:
    path = _DIR / f"{lang}.json"
    if not path.is_file():
        return None
    return Translation(lang, json.loads(path.read_text(encoding="utf-8")))


def _translate_text_nodes(html: str, tr: Translation) -> str:
    # A text node is whatever sits between a ">" and the next "<". Scripts
    # and styles are skipped whole: their "text" is code.
    out: list[str] = []
    pos = 0
    skip = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
    node = re.compile(r">([^<>]+)<")
    for block in skip.finditer(html):
        out.append(_translate_span(html[pos : block.start()], tr, node))
        out.append(block.group(0))
        pos = block.end()
    out.append(_translate_span(html[pos:], tr, node))
    return "".join(out)


def _translate_span(chunk: str, tr: Translation, node: re.Pattern[str]) -> str:
    def swap(m: re.Match[str]) -> str:
        raw = m.group(1)
        text = _WS.sub(" ", raw).strip()
        if not text:
            return m.group(0)
        hit = tr.lookup(text)
        if hit is None:
            return m.group(0)
        # Keep the surrounding whitespace exactly: it is often the only thing
        # separating an inline element from the next word.
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()) :]
        return f">{lead}{hit}{trail}<"

    return node.sub(swap, chunk)


def _translate_attributes(html: str, tr: Translation) -> str:
    names = "|".join(_ATTRS)
    pattern = re.compile(rf'\s({names})="([^"]*)"')

    def swap(m: re.Match[str]) -> str:
        hit = tr.lookup(_WS.sub(" ", m.group(2)).strip())
        if hit is None:
            return m.group(0)
        return f' {m.group(1)}="{escape(hit, quote=True)}"'

    return pattern.sub(swap, html)


def _translate_head(html: str, tr: Translation) -> str:
    def swap_title(m: re.Match[str]) -> str:
        hit = tr.head.get(m.group(1).strip())
        return f"<title>{hit}</title>" if hit else m.group(0)

    html = re.sub(r"<title>([^<]*)</title>", swap_title, html, count=1)

    def swap_content(m: re.Match[str]) -> str:
        hit = tr.head.get(m.group(2).strip())
        return f'{m.group(1)}content="{escape(hit, quote=True)}"' if hit else m.group(0)

    return re.sub(
        r'(<meta\s+(?:name|property)="(?:description|og:title|og:description|twitter:title|twitter:description)"\s+)content="([^"]*)"',
        swap_content,
        html,
    )


def _path(prefix: str, path_en: str) -> str:
    """The page's path in one language: "/" -> "/hi", "/about" -> "/hi/about"."""
    if path_en == "/":
        return prefix or "/"
    return f"{prefix}{path_en}"


def _alternates(site: str, path_en: str) -> str:
    """``hreflang`` links for every language plus x-default."""
    links = []
    for code, (_name, prefix) in LANGUAGES.items():
        links.append(f'<link rel="alternate" hreflang="{code}" href="{site}{_path(prefix, path_en)}">')
    links.append(f'<link rel="alternate" hreflang="x-default" href="{site}{path_en}">')
    return "\n".join(links)


def _switch_html(lang: str, path_en: str) -> str:
    """The EN | हिंदी switch for the nav: links, not a script, so it works
    without JavaScript and search engines follow it."""
    parts = []
    for code, (name, prefix) in LANGUAGES.items():
        label = "EN" if code == "en" else name
        href = _path(prefix, path_en)
        if code == lang:
            parts.append(f'<span class="lang-on" aria-current="true" lang="{code}">{label}</span>')
        else:
            parts.append(
                f'<a href="{href}" hreflang="{code}" lang="{code}" '
                f'onclick="try{{localStorage.setItem(\'site-lang\',\'{code}\')}}catch(e){{}}">{label}</a>'
            )
    sep = ' <span class="lang-sep">|</span> '
    return f'<li class="lang-switch" aria-label="Language">{sep.join(parts)}</li>'


_SWITCH_CSS = """
<style>
.lang-switch{display:inline-flex;align-items:center;gap:6px;font-size:.82rem;
  padding-left:14px;margin-left:6px;border-left:1px solid rgba(255,255,255,.12)}
.lang-switch a{color:var(--tm)}
.lang-switch a:hover{color:var(--t)}
.lang-switch .lang-on{color:var(--pl);font-weight:600}
.lang-switch .lang-sep{color:var(--td)}
@media(max-width:900px){.lang-switch{border-left:0;padding-left:0;margin-left:0}}
</style>
"""

#: A first-time visitor whose browser is Hindi is sent to the Hindi page once;
#: after that their own choice (the switch) wins. Only ever runs on the
#: English root, only when nothing is stored, and never for crawlers (they
#: get the hreflang links instead).
_REDIRECT_JS = """
<script>
(function(){
  try{
    if(localStorage.getItem('site-lang'))return;
    if(/bot|crawl|spider/i.test(navigator.userAgent))return;
    var l=(navigator.language||'').toLowerCase();
    var langs=(navigator.languages||[l]).map(function(x){return String(x).toLowerCase()});
    if(langs.length&&langs[0].indexOf('hi')===0){
      localStorage.setItem('site-lang','hi');
      location.replace('/hi'+location.pathname.replace(/^\\/$/,'')+location.hash);
    }
  }catch(e){}
})();
</script>
"""


def localize(html: str, lang: str, *, path_en: str, site: str) -> str:
    """Return the page in ``lang``.

    ``path_en`` is the page's English path (``/`` or ``/about``); ``site`` the
    absolute origin for the ``hreflang`` links. English pages get the switch,
    the alternates and the one-time redirect; other languages get the
    translation too, with internal links pointed at their own language.
    """
    if lang not in LANGUAGES:
        lang = DEFAULT_LANG
    prefix = LANGUAGES[lang][1]

    if lang != DEFAULT_LANG:
        tr = load(lang)
        if tr is not None:
            html = re.sub(r'<html\s+lang="[^"]*"', f'<html lang="{lang}"', html, count=1)
            html = _translate_head(html, tr)
            html = _translate_text_nodes(html, tr)
            html = _translate_attributes(html, tr)
            # Internal links stay inside the language: "/about" -> "/hi/about",
            # "/#glasses" -> "/hi#glasses". Downloads, legal pages, media untouched.
            html = html.replace('href="/about"', f'href="{prefix}/about"')
            html = re.sub(r'href="/(#[^"]*)?"', lambda m: f'href="{prefix}{m.group(1) or ""}"', html)

    # Nav switch: after the About link in both pages' navs (the About page
    # marks its own link aria-current, so match either form; on a Hindi page
    # the link was just rewritten to /hi/about).
    html = re.sub(
        r'(<li><a href="(?:/[a-z]{2})?/about"[^>]*>[^<]*</a></li>)',
        lambda m: m.group(1) + _switch_html(lang, path_en),
        html,
        count=1,
    )
    # hreflang alternates + the switch's stylesheet at the end of <head>; the
    # one-time redirect only on the English root.
    head_extra = _alternates(site, path_en) + "\n" + _SWITCH_CSS
    if lang == DEFAULT_LANG:
        head_extra += _REDIRECT_JS
    return html.replace("</head>", head_extra + "</head>", 1)
