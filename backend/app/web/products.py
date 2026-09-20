"""Product photography and video for the glasses, rendered from one manifest.

The site asks for AED 300–350 and, until now, showed a 🕶 emoji where the
product shot belongs. Nobody buys hardware they have not seen, so the spec
cards get a real gallery: a video if one exists, then stills, with a thumbnail
strip and a lightbox.

The photographs are not in the repo — they are shot per model and dropped into
a media directory. That is the whole point of this module: it *discovers* what
is on disk rather than hard-coding filenames into HTML. A card shows exactly
the shots that exist. A model with no media renders nothing at all, which is
why adding this module changed not one byte of the page it ships with.

Layout the media directory is expected to have (see MEDIA.md)::

    media/
      l801/
        front.jpg  front-400.webp  front-800.webp  front-1200.webp ...
        video.mp4  video.webm  video-poster.jpg
      l802/
        ...

``scripts/optimize_media.py`` turns one photographer's JPEG into that whole
spread, so nobody has to name files by hand.

Serving lives in ``router.py`` (``GET /media/{model}/{file}``); this module only
decides what the page asks for.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from app.config import Settings

# Responsive widths we look for, narrowest first. A file named `front-800.webp`
# is the 800px-wide rendition of the `front` shot; `front.webp` (no suffix) is
# the single-size fallback for anyone who has not run the optimizer yet.
_WIDTHS: tuple[int, ...] = (400, 800, 1200, 1600)

# Still formats in `<picture>` order: the browser takes the first type it can
# decode, so the modern, smaller formats are listed before the universal one.
# The last entry is what the bare <img src> points at — it must be a format
# every browser has understood for a decade.
_MODERN_STILLS: tuple[tuple[str, str], ...] = (
    (".avif", "image/avif"),
    (".webp", "image/webp"),
)
_FALLBACK_STILLS: tuple[str, ...] = (".jpg", ".jpeg", ".png")

# Video renditions, best-compression first for the same reason.
_VIDEOS: tuple[tuple[str, str], ...] = (
    (".webm", "video/webm"),
    (".mp4", "video/mp4"),
)
_POSTERS: tuple[str, ...] = (".webp", ".jpg", ".jpeg", ".png")

# Every file extension this module will ever reference. `router.py` reads it to
# decide what it is willing to serve, so the two can never drift apart.
ALLOWED_SUFFIXES: frozenset[str] = frozenset(
    [ext for ext, _ in _MODERN_STILLS]
    + list(_FALLBACK_STILLS)
    + [ext for ext, _ in _VIDEOS]
)

MEDIA_TYPES: dict[str, str] = {
    ".avif": "image/avif",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}

# The shots each card can show, in the order they appear, with the alt text
# that describes them. Alt text is written here and not derived from filenames
# because "front" is not a description — a screen reader (and Google Images)
# needs the sentence. A shot listed here that has no file is simply skipped.
_SHOTS: tuple[tuple[str, str], ...] = (
    ("front", "{name} smart glasses seen straight on"),
    ("angle", "{name} at a three-quarter angle, showing the temple and hinge"),
    ("camera", "Close-up of the {name} camera module built into the frame"),
    ("lenses", "{name} with the clear anti-blue-light lenses fitted"),
    ("side", "{name} from the side, showing the temple and speaker"),
    ("back", "{name} from behind, showing the inside of the temples"),
    ("top", "{name} from above"),
    ("folded", "{name} folded, showing how slim the frame sits"),
    ("worn", "{name} being worn, front view"),
    ("worn-side", "{name} being worn, side view"),
    ("red", "{name} in the red frame"),
    ("cream", "{name} in the cream frame"),
    ("case", "The {name} charging case"),
    ("charging", "{name} charging in its case, with the case charging a phone"),
    ("lifestyle", "{name} in everyday use"),
    ("scale", "{name} held in a hand, for a sense of size and weight"),
    ("box", "What is in the {name} box"),
)

# Slug -> the model it belongs to. The slug is the media sub-directory name and
# the URL segment; the name is what the alt text says.
MODELS: dict[str, str] = {
    "l801": "L801 Business",
    "l802": "L802 Premium",
    "gs4": "Farry-GS4",
    "gs5": "GS5 MAX",
}

# What each model costs — the ONE place the price lives. Three fixed price
# lists, not one converted at today's rate: a buyer in the UAE pays dirhams,
# one in India rupees, everyone else dollars (Faraz, 2026-09-20), and the
# figure they see is the figure they are charged. The spec cards print it,
# the cart adds it up, the checkout charges it (modules/shop). Change it
# here, nowhere else.
CURRENCIES = ("AED", "INR", "USD")
PRICES: dict[str, dict[str, int]] = {
    #        AED   INR    USD
    "l801": {"AED": 300, "INR": 7999, "USD": 89},
    "l802": {"AED": 350, "INR": 9499, "USD": 99},
    "gs4": {"AED": 350, "INR": 9499, "USD": 99},
    "gs5": {"AED": 450, "INR": 11999, "USD": 129},
}
# The dirham list on its own — what the spec cards are written in.
PRICES_AED: dict[str, int] = {slug: p["AED"] for slug, p in PRICES.items()}

# What delivery costs, by destination country, in each currency. "*" is
# everywhere else. Free within the UAE; a courier to India or the rest of
# the world is charged as a line of its own on the Stripe page and in the
# order. An order can be paid in one currency and delivered to another
# country (an Indian in Dubai sending a pair home pays rupees + India
# delivery).
DELIVERY: dict[str, dict[str, int]] = {
    "AE": {"AED": 0, "INR": 0, "USD": 0},
    "IN": {"AED": 75, "INR": 1500, "USD": 20},
    "*": {"AED": 110, "INR": 2500, "USD": 30},
}


def delivery_charge(country: str, currency: str) -> int:
    """Whole units of ``currency`` to deliver to ``country``."""
    table = DELIVERY.get(country.upper()) or DELIVERY["*"]
    return int(table.get(currency.upper(), 0))

# Colour choices, for the models that come in more than one.
COLOURS: dict[str, list[str]] = {
    "gs5": ["Black", "Red", "Cream"],
}

# Product catalogs (one PDF per model) live next to the photographs, in
# ``<media root>/catalogs/<slug>.pdf``. Like the photographs they are
# discovered, never assumed: a model without a catalog gets no link.
CATALOG_DIR = "catalogs"

# `sizes` tells the browser how wide the image will actually be *before* it has
# any layout, so it can pick the right rendition on the first try. The cards sit
# two-up above 900px and full width below it — the same breakpoint `.specs-grid`
# uses.
_SIZES = "(max-width: 900px) 92vw, 44vw"
# The thumbnail strip: 56px boxes, so the 400px rendition is already 7x.
_THUMB_SIZES = "56px"


class Gallery:
    """The media that actually exists on disk for one model."""

    __slots__ = ("name", "poster", "slug", "stills", "video")

    def __init__(self, slug: str, name: str) -> None:
        self.slug = slug
        self.name = name
        # [(shot, alt, {ext: {width|0: filename}})] in _SHOTS order.
        self.stills: list[tuple[str, str, dict[str, dict[int, str]]]] = []
        self.video: list[tuple[str, str]] = []  # [(filename, mime)]
        self.poster: str | None = None

    def __bool__(self) -> bool:
        return bool(self.stills or self.video)

    @property
    def count(self) -> int:
        return len(self.stills) + (1 if self.video else 0)


def _scan_model(directory: Path, slug: str, name: str) -> Gallery:
    """Read one model's media directory into a Gallery (never raises)."""
    gallery = Gallery(slug, name)
    try:
        present = {p.name for p in directory.iterdir() if p.is_file()}
    except OSError:
        # No directory yet — the overwhelmingly common case before a photoshoot.
        return gallery

    for shot, alt_template in _SHOTS:
        # {".webp": {800: "front-800.webp", 0: "front.webp"}, ...}
        found: dict[str, dict[int, str]] = {}
        for ext in ALLOWED_SUFFIXES:
            if ext in (".mp4", ".webm"):
                continue
            by_width: dict[int, str] = {}
            if f"{shot}{ext}" in present:
                by_width[0] = f"{shot}{ext}"
            for width in _WIDTHS:
                candidate = f"{shot}-{width}{ext}"
                if candidate in present:
                    by_width[width] = candidate
            if by_width:
                found[ext] = by_width
        if found:
            gallery.stills.append((shot, alt_template.format(name=name), found))

    for ext, mime in _VIDEOS:
        if f"video{ext}" in present:
            gallery.video.append((f"video{ext}", mime))
    for ext in _POSTERS:
        if f"video-poster{ext}" in present:
            gallery.poster = f"video-poster{ext}"
            break

    return gallery


def media_root(settings: Settings) -> Path:
    """Where the product media lives (setting override, else the package dir).

    Mirrors how ``router._apk_dir`` resolves builds: production mounts a volume
    and points ``media_dir`` at it, so photographs never have to be baked into
    a Docker image or committed to git.
    """
    configured = getattr(settings, "media_dir", None)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "media"


# Scanning is a handful of `iterdir` calls, but the landing page is rendered on
# every GET / and photographs change about twice a year. The cache is keyed on
# the directories' mtimes, so dropping a new file in is picked up on the next
# request without a restart, while an unchanged directory costs two `stat`s.
_cache: dict[tuple, dict[str, Gallery]] = {}


def _signature(root: Path) -> tuple:
    stamps = []
    for slug in MODELS:
        try:
            stamps.append((slug, (root / slug).stat().st_mtime_ns))
        except OSError:
            stamps.append((slug, -1))
    return (str(root), tuple(stamps))


def galleries(settings: Settings) -> dict[str, Gallery]:
    """Every model's on-disk media, cached until the directories change."""
    root = media_root(settings)
    signature = _signature(root)
    hit = _cache.get(signature)
    if hit is None:
        hit = {
            slug: _scan_model(root / slug, slug, name)
            for slug, name in MODELS.items()
        }
        # Only the current signature is ever useful; keeping older ones would
        # leak a dict per media change for the life of the process.
        _cache.clear()
        _cache[signature] = hit
    return hit


def _url(slug: str, filename: str) -> str:
    return f"/media/{slug}/{escape(filename, quote=True)}"


def catalog_path(settings: Settings, slug: str) -> Path | None:
    """The model's catalog PDF on disk, or None when there is none."""
    if slug not in MODELS:
        return None
    path = media_root(settings) / CATALOG_DIR / f"{slug}.pdf"
    return path if path.is_file() else None


def catalog_html(settings: Settings, slug: str) -> str:
    """The "Product catalog (PDF)" link for one spec card, or ""."""
    path = catalog_path(settings, slug)
    if path is None:
        return ""
    size_mb = path.stat().st_size / 1_048_576
    name = escape(MODELS[slug], quote=True)
    return (
        f'<div class="pm-catalog"><a href="/catalogs/{slug}.pdf" target="_blank" '
        f'rel="noopener noreferrer" aria-label="{name} product catalog, PDF">'
        f'<svg viewBox="0 0 24 24" width="16" height="16" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">'
        f'<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
        f'<path d="M14 3v5h5M9 13h6M9 17h6"/></svg>'
        f"<span>Product catalog</span><small>PDF · {size_mb:.1f} MB</small></a></div>"
    )


def _picture(
    slug: str,
    alt: str,
    by_ext: dict[str, dict[int, str]],
    eager: bool,
    sizes: str = _SIZES,
) -> str:
    """A <picture> that offers AVIF/WebP and falls back to JPEG/PNG.

    ``sizes`` is how wide the image will be laid out; a thumbnail passes its
    own so the browser fetches the 400px file for a 56px box instead of the
    same 800px one the slide already uses.
    """
    sources = []
    for ext, mime in _MODERN_STILLS:
        by_width = by_ext.get(ext)
        if not by_width:
            continue
        sized = [(w, f) for w, f in sorted(by_width.items()) if w]
        if sized:
            srcset = ", ".join(f"{_url(slug, f)} {w}w" for w, f in sized)
            sources.append(
                f'<source type="{mime}" srcset="{srcset}" sizes="{sizes}">'
            )
        elif 0 in by_width:
            sources.append(
                f'<source type="{mime}" srcset="{_url(slug, by_width[0])}">'
            )

    # The <img> is what every browser falls back to, so it must be a universal
    # format; only if the shoot produced nothing but WebP/AVIF do we point it
    # at one of those rather than render a broken image.
    fallback_ext = next(
        (ext for ext in _FALLBACK_STILLS if ext in by_ext),
        next(iter(by_ext)),
    )
    by_width = by_ext[fallback_ext]
    src = by_width.get(max(by_width)) or by_width[0]
    sized = [(w, f) for w, f in sorted(by_width.items()) if w]
    srcset = (
        f' srcset="{", ".join(f"{_url(slug, f)} {w}w" for w, f in sized)}"'
        f' sizes="{sizes}"'
        if sized
        else ""
    )
    loading = "eager" if eager else "lazy"
    return (
        f"<picture>{''.join(sources)}"
        f'<img src="{_url(slug, src)}"{srcset} alt="{escape(alt, quote=True)}" '
        f'loading="{loading}" decoding="async">'
        f"</picture>"
    )


def _video(gallery: Gallery) -> str:
    poster = (
        f' poster="{_url(gallery.slug, gallery.poster)}"' if gallery.poster else ""
    )
    sources = "".join(
        f'<source src="{_url(gallery.slug, name)}" type="{mime}">'
        for name, mime in gallery.video
    )
    # `preload="none"` because this sits below the fold on a page that already
    # ships 66KB of HTML: the poster is all a visitor sees until they press
    # play, and on a metered Indian mobile connection that difference is real.
    return (
        f'<video class="pm-video" controls playsinline preload="none"{poster}>'
        f"{sources}"
        f"Your browser cannot play this video."
        f"</video>"
    )


def gallery_html(gallery: Gallery) -> str:
    """The media block for one spec card, or "" when there is no media."""
    if not gallery:
        return ""

    slides: list[str] = []
    thumbs: list[str] = []
    index = 0

    if gallery.video:
        slides.append(
            f'<div class="pm-slide is-on" data-i="{index}" role="group" '
            f'aria-label="Video, slide {index + 1} of {gallery.count}">'
            f"{_video(gallery)}</div>"
        )
        poster = (
            f'<img src="{_url(gallery.slug, gallery.poster)}" alt="" '
            f'loading="lazy" decoding="async">'
            if gallery.poster
            else '<span class="pm-play-ico" aria-hidden="true">▶</span>'
        )
        thumbs.append(
            f'<button type="button" class="pm-thumb is-on" data-i="{index}" '
            f'aria-label="Play the {escape(gallery.name, quote=True)} video">'
            f'{poster}<span class="pm-thumb-play" aria-hidden="true">▶</span>'
            f"</button>"
        )
        index += 1

    for shot, alt, by_ext in gallery.stills:
        on = " is-on" if index == 0 else ""
        slides.append(
            f'<div class="pm-slide{on}" data-i="{index}" role="group" '
            f'aria-label="Photo {index + 1} of {gallery.count}">'
            f"{_picture(gallery.slug, alt, by_ext, eager=False)}</div>"
        )
        thumbs.append(
            f'<button type="button" class="pm-thumb{on}" data-i="{index}" '
            f'aria-label="{escape(alt, quote=True)}">'
            f"{_picture(gallery.slug, '', by_ext, eager=False, sizes=_THUMB_SIZES)}</button>"
        )
        index += 1

    # One slide needs no carousel chrome — arrows that cannot go anywhere and a
    # thumbnail strip of one are just noise.
    nav = ""
    strip = ""
    if gallery.count > 1:
        nav = (
            '<button type="button" class="pm-nav pm-prev" '
            'aria-label="Previous image">‹</button>'
            '<button type="button" class="pm-nav pm-next" '
            'aria-label="Next image">›</button>'
        )
        strip = f'<div class="pm-thumbs">{"".join(thumbs)}</div>'

    label = escape(gallery.name, quote=True)
    return (
        f'<div class="pm" data-pm="{gallery.slug}" role="region" '
        f'aria-roledescription="carousel" aria-label="{label} photos">'
        f'<div class="pm-stage">{"".join(slides)}{nav}'
        f'<button type="button" class="pm-zoom" aria-label="View larger">⛶</button>'
        f"</div>{strip}</div>"
    )


# The stylesheet and the behaviour live here rather than in index.html for one
# reason: a site with no photographs yet must render byte-for-byte as it did
# before. Both are injected only when at least one model has media, so the
# shipped page carries no dead CSS and no script that observes nothing.
_CSS = """
<style>
/* ═══════════════════════════════════════════
   PRODUCT MEDIA — gallery on the spec cards
═══════════════════════════════════════════ */
/* The spec cards are grid items, and a grid item's `min-width:auto` lets its
   widest child set the card's width — the thumbnail strip did exactly that and
   pushed the specs off the side of a phone screen. Scoped to this stylesheet,
   which only ships when a gallery does. */
.spec-card,.spec-inner,.pm,.pm-stage,.pm-thumbs{min-width:0}
.pm{position:relative;background:#040A14;border-bottom:1px solid rgba(0,212,170,.07)}
/* 3:2 on the wide layout so the photograph leads the card without burying the
   spec table below the fold; squarer on a phone, where a taller frame shows
   more of the product per scroll. */
.pm-stage{position:relative;aspect-ratio:3/2;overflow:hidden;
  background:radial-gradient(circle at 50% 42%,rgba(0,212,170,.07),transparent 62%)}
.pm-slide{position:absolute;inset:0;opacity:0;visibility:hidden;
  transition:opacity .35s ease}
.pm-slide.is-on{opacity:1;visibility:visible}
.pm-slide picture{display:block;width:100%;height:100%}
/* `contain`, never `cover`: a cropped product shot hides the very detail the
   photograph was taken for. */
.pm-slide img,.pm-video{width:100%;height:100%;display:block;object-fit:contain}
.pm-video{background:#000;cursor:pointer}
.pm-stage>.pm-slide img{cursor:zoom-in}

/* Arrows + zoom: invisible until the card is hovered, always visible on touch
   and always reachable by keyboard. */
.pm-nav,.pm-zoom{position:absolute;z-index:3;border:1px solid rgba(0,212,170,.22);
  background:rgba(4,10,20,.72);color:var(--t);cursor:pointer;
  backdrop-filter:blur(6px);opacity:0;transition:opacity .25s ease,background .2s ease}
.pm-nav{top:50%;transform:translateY(-50%);width:38px;height:38px;border-radius:50%;
  font-size:1.5rem;line-height:1;display:flex;align-items:center;justify-content:center;
  font-family:var(--fd);padding-bottom:3px}
.pm-prev{left:12px}.pm-next{right:12px}
.pm-zoom{right:12px;bottom:12px;width:32px;height:32px;border-radius:9px;
  font-size:.82rem;display:flex;align-items:center;justify-content:center}
.pm:hover .pm-nav,.pm:hover .pm-zoom,
.pm-nav:focus-visible,.pm-zoom:focus-visible{opacity:1}
.pm-nav:hover,.pm-zoom:hover{background:rgba(0,212,170,.2);border-color:rgba(0,212,170,.5)}
.pm-nav:focus-visible,.pm-zoom:focus-visible{outline:2px solid var(--pl);outline-offset:2px}
.pm-zoom.is-hidden{display:none}

/* Thumbnail strip */
.pm-thumbs{display:flex;gap:8px;padding:12px 14px;overflow-x:auto;
  scrollbar-width:none;background:rgba(3,8,16,.6)}
.pm-thumbs::-webkit-scrollbar{display:none}
.pm-thumb{position:relative;flex:0 0 auto;width:56px;height:44px;padding:0;
  border-radius:8px;overflow:hidden;cursor:pointer;background:#060D1A;
  border:1px solid rgba(0,212,170,.1);opacity:.55;transition:.22s}
.pm-thumb img{width:100%;height:100%;object-fit:contain;display:block}
.pm-thumb:hover{opacity:.85}
.pm-thumb.is-on{opacity:1;border-color:rgba(0,212,170,.55);
  box-shadow:0 0 12px rgba(0,212,170,.22)}
.pm-thumb:focus-visible{outline:2px solid var(--pl);outline-offset:2px;opacity:1}
.pm-thumb-play,.pm-play-ico{position:absolute;inset:0;display:flex;
  align-items:center;justify-content:center;font-size:.72rem;color:#fff;
  background:rgba(3,9,18,.45);text-shadow:0 0 6px rgba(0,0,0,.8)}

/* Lightbox */
.pm-lb{position:fixed;inset:0;z-index:9999;display:none;
  align-items:center;justify-content:center;padding:clamp(16px,4vw,48px);
  background:rgba(3,9,18,.96);backdrop-filter:blur(10px)}
.pm-lb.is-on{display:flex}
.pm-lb img{max-width:100%;max-height:100%;object-fit:contain;
  border-radius:10px;box-shadow:0 24px 80px rgba(0,0,0,.6)}
.pm-lb-close,.pm-lb-nav{position:absolute;background:rgba(6,14,28,.85);
  border:1px solid rgba(0,212,170,.25);color:var(--t);cursor:pointer;
  border-radius:50%;width:44px;height:44px;font-size:1.5rem;line-height:1;
  display:flex;align-items:center;justify-content:center;font-family:var(--fd)}
.pm-lb-close{top:18px;right:18px;font-size:1.3rem}
.pm-lb-nav{top:50%;transform:translateY(-50%);padding-bottom:4px}
.pm-lb-prev{left:18px}.pm-lb-next{right:18px}
.pm-lb-close:hover,.pm-lb-nav:hover{background:rgba(0,212,170,.22)}
.pm-lb-close:focus-visible,.pm-lb-nav:focus-visible{
  outline:2px solid var(--pl);outline-offset:2px}
.pm-lb-cap{position:absolute;bottom:18px;left:50%;transform:translateX(-50%);
  font-size:.78rem;color:var(--tm);text-align:center;max-width:80%}

@media(max-width:900px){
  .pm-nav,.pm-zoom{opacity:1}
  .pm-stage{aspect-ratio:4/3}
  .pm-lb-nav{width:38px;height:38px}
}
@media(prefers-reduced-motion:reduce){
  .pm-slide{transition:none}
}
</style>
"""

# `hidden` on the overlay keeps it out of the accessibility tree until it is
# opened; the CSS `display` toggle alone would leave it focusable to a screen
# reader on a page that looks, to a sighted visitor, entirely closed.
_JS = """
<div class="pm-lb" id="pm-lb" hidden role="dialog" aria-modal="true"
     aria-label="Product photo">
  <img id="pm-lb-img" src="" alt="">
  <button type="button" class="pm-lb-close" id="pm-lb-close"
          aria-label="Close">✕</button>
  <button type="button" class="pm-lb-nav pm-lb-prev" aria-label="Previous">‹</button>
  <button type="button" class="pm-lb-nav pm-lb-next" aria-label="Next">›</button>
  <p class="pm-lb-cap" id="pm-lb-cap"></p>
</div>
<script>
/* ═══════════════════════════════════════════════════════════════
   PRODUCT GALLERY — slide switching, swipe, lightbox
   Injected only when a model actually has media on disk.
═══════════════════════════════════════════════════════════════ */
(function(){
  var lb=document.getElementById('pm-lb');
  if(!lb) return;
  var lbImg=document.getElementById('pm-lb-img');
  var lbCap=document.getElementById('pm-lb-cap');
  var lbPrev=lb.querySelector('.pm-lb-prev');
  var lbNext=lb.querySelector('.pm-lb-next');
  var shots=[], at=0, opener=null;

  function paint(){
    if(!shots.length) return;
    at=(at+shots.length)%shots.length;
    var s=shots[at];
    lbImg.src=s.src; lbImg.alt=s.alt; lbCap.textContent=s.alt;
    var many=shots.length>1;
    lbPrev.hidden=!many; lbNext.hidden=!many;
  }
  function openLb(list,index,from){
    shots=list; at=index; opener=from||null;
    lb.hidden=false; lb.classList.add('is-on');
    document.body.style.overflow='hidden';
    paint(); lb.querySelector('#pm-lb-close').focus();
  }
  function closeLb(){
    lb.classList.remove('is-on'); lb.hidden=true;
    document.body.style.overflow='';
    lbImg.src='';
    if(opener&&opener.focus) opener.focus();
    opener=null;
  }
  lb.querySelector('#pm-lb-close').addEventListener('click',closeLb);
  lbPrev.addEventListener('click',function(){at--;paint()});
  lbNext.addEventListener('click',function(){at++;paint()});
  lb.addEventListener('click',function(e){ if(e.target===lb) closeLb() });
  document.addEventListener('keydown',function(e){
    if(lb.hidden) return;
    if(e.key==='Escape'){ closeLb() }
    else if(e.key==='ArrowLeft'){ at--; paint() }
    else if(e.key==='ArrowRight'){ at++; paint() }
    else if(e.key==='Tab'){ e.preventDefault(); }
  });

  document.querySelectorAll('.pm').forEach(function(pm){
    var slides=[].slice.call(pm.querySelectorAll('.pm-slide'));
    var thumbs=[].slice.call(pm.querySelectorAll('.pm-thumb'));
    var zoom=pm.querySelector('.pm-zoom');
    var stage=pm.querySelector('.pm-stage');
    var i=slides.findIndex(function(s){return s.classList.contains('is-on')});
    if(i<0) i=0;

    function show(next){
      next=(next+slides.length)%slides.length;
      if(next===i) return;
      /* A video left playing behind a photo is the classic carousel bug —
         you hear a product you can no longer see. */
      var v=slides[i].querySelector('video');
      if(v&&!v.paused) v.pause();
      slides[i].classList.remove('is-on');
      if(thumbs[i]) thumbs[i].classList.remove('is-on');
      i=next;
      slides[i].classList.add('is-on');
      if(thumbs[i]){
        thumbs[i].classList.add('is-on');
        if(thumbs[i].scrollIntoView) thumbs[i].scrollIntoView(
          {block:'nearest',inline:'nearest'});
      }
      if(zoom) zoom.classList.toggle('is-hidden',
        !slides[i].querySelector('img'));
    }

    var prev=pm.querySelector('.pm-prev'), next=pm.querySelector('.pm-next');
    if(prev) prev.addEventListener('click',function(){show(i-1)});
    if(next) next.addEventListener('click',function(){show(i+1)});
    thumbs.forEach(function(t,n){
      t.addEventListener('click',function(){show(n)});
    });

    /* Every still in this card, for the lightbox. `currentSrc` is what the
       browser actually chose off the srcset, so the overlay reuses the
       rendition already in cache instead of fetching a second one. */
    function stills(){
      var out=[];
      slides.forEach(function(s,n){
        var img=s.querySelector('img');
        if(img) out.push({src:img.currentSrc||img.src,alt:img.alt,slide:n});
      });
      return out;
    }
    function openHere(){
      var list=stills();
      var pos=0;
      for(var n=0;n<list.length;n++){ if(list[n].slide===i){ pos=n; break } }
      if(list.length) openLb(list,pos,zoom);
    }
    if(zoom) zoom.addEventListener('click',openHere);
    stage.addEventListener('click',function(e){
      if(e.target.tagName==='IMG') openHere();
    });

    /* Swipe — the only way most visitors will ever move these on a phone. */
    var x0=null,y0=null;
    stage.addEventListener('touchstart',function(e){
      x0=e.touches[0].clientX; y0=e.touches[0].clientY;
    },{passive:true});
    stage.addEventListener('touchend',function(e){
      if(x0===null) return;
      var dx=e.changedTouches[0].clientX-x0;
      var dy=e.changedTouches[0].clientY-y0;
      /* Horizontal intent only, or every attempt to scroll the page past the
         card would flip the photo. */
      if(Math.abs(dx)>40&&Math.abs(dx)>Math.abs(dy)) show(i+(dx<0?1:-1));
      x0=y0=null;
    },{passive:true});

    if(zoom) zoom.classList.toggle('is-hidden',!slides[i].querySelector('img'));
  });
})();
</script>
"""


# The catalog link's own stylesheet: it ships when a catalog exists, which is
# independent of whether any photographs do.
_CATALOG_CSS = """
<style>
.pm-catalog{padding:0 22px 20px}
.pm-catalog a{display:flex;align-items:center;gap:10px;padding:11px 16px;
  border-radius:12px;border:1px solid rgba(0,212,170,.18);
  background:rgba(0,212,170,.05);color:var(--t);text-decoration:none;
  font-size:.86rem;font-weight:600;transition:.22s}
.pm-catalog a svg{color:var(--pl);flex:0 0 auto}
.pm-catalog a small{margin-left:auto;font-size:.72rem;font-weight:400;color:var(--tm)}
.pm-catalog a:hover{background:rgba(0,212,170,.12);border-color:rgba(0,212,170,.4)}
.pm-catalog a:focus-visible{outline:2px solid var(--pl);outline-offset:2px}
</style>
"""


def render(html: str, settings: Settings) -> str:
    """Fill the landing page's product-media and catalog placeholders.

    With no media on disk every placeholder resolves to the empty string, so
    the page is exactly what it was before this module existed — no stylesheet,
    no script, no markup.
    """
    found = galleries(settings)
    for slug, gallery in found.items():
        html = html.replace(f"<!--PRODUCT_MEDIA:{slug}-->", gallery_html(gallery))
    has_catalog = False
    for slug in MODELS:
        link = catalog_html(settings, slug)
        has_catalog = has_catalog or bool(link)
        html = html.replace(f"<!--CATALOG:{slug}-->", link)
    has_media = any(found.values())
    styles = (_CSS if has_media else "") + (_CATALOG_CSS if has_catalog else "")
    return html.replace("<!--PRODUCT_MEDIA_CSS-->", styles).replace(
        "<!--PRODUCT_MEDIA_JS-->", _JS if has_media else ""
    )
