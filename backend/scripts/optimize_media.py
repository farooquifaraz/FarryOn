#!/usr/bin/env python3
"""Turn the photographer's originals into every rendition the site serves.

A product shoot comes back as a handful of very large JPEGs. The website wants
each of them at four widths, in WebP (and AVIF where Pillow can write it), plus
one compressed JPEG so that no browser is left without a picture. Doing that by
hand is nine files per shot and a naming convention nobody remembers, so this
does it.

    # Put the originals in one folder per model, named after the shot:
    #   originals/l801/front.jpg, originals/l801/camera.jpg, ...
    python scripts/optimize_media.py originals/ app/web/media/

    # See what it would write, without writing anything:
    python scripts/optimize_media.py originals/ app/web/media/ --dry-run

Shot names are the ones ``app/web/products.py`` knows about — run with
``--list`` to see them. An unrecognised name is reported and skipped rather
than silently ending up on disk where nothing will ever render it.

Video is not processed here: re-encoding it well needs ffmpeg, not Pillow.
``MEDIA.md`` has the two ffmpeg commands that produce ``video.mp4`` and
``video.webm``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Run from the repo's backend/ directory so `app` imports the same way the
# server does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.web.products import _SHOTS, _WIDTHS, MODELS

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the script is the only caller
    sys.exit("Pillow is required: pip install Pillow")

SHOT_NAMES = [shot for shot, _ in _SHOTS]
ORIGINAL_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

# Quality is per-format, not one number: WebP at 82 is visually indistinguishable
# from the original at a third of JPEG's size, while the JPEG fallback needs 86
# to avoid ringing on the high-contrast edge between a dark frame and a white
# backdrop — exactly the edge every one of these photographs has.
WEBP_QUALITY = 82
AVIF_QUALITY = 60
JPEG_QUALITY = 86


def _kb(path: Path) -> str:
    return f"{path.stat().st_size / 1024:,.0f}KB"


def _save(image: Image.Image, out: Path, dry: bool) -> tuple[bool, str]:
    """Write one rendition. Returns (written, note)."""
    if dry:
        return False, "would write"
    out.parent.mkdir(parents=True, exist_ok=True)
    suffix = out.suffix.lower()
    try:
        if suffix == ".webp":
            image.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
        elif suffix == ".avif":
            image.save(out, "AVIF", quality=AVIF_QUALITY)
        else:
            image.convert("RGB").save(
                out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True
            )
    except (OSError, KeyError, ValueError) as exc:
        # AVIF needs a Pillow built with libavif; missing it is normal and not
        # worth failing a run over, since WebP already covers ~97% of browsers.
        return False, f"skipped ({exc.__class__.__name__})"
    return True, _kb(out)


def process_shot(src: Path, out_dir: Path, dry: bool, avif: bool) -> int:
    with Image.open(src) as im:
        im = im.convert("RGB")
        width, height = im.size
        shot = src.stem.lower()
        print(f"  {shot:<12} {width}x{height}  {_kb(src)}")

        written = 0
        for target in _WIDTHS:
            # Never upscale: a 400px original blown up to 1600 is a bigger file
            # that looks worse than the one the browser would have made itself.
            if target > width:
                print(f"     {target:>5}w  skipped (original is only {width}px)")
                continue
            scaled = im.resize(
                (target, round(height * target / width)), Image.LANCZOS
            )
            formats = [".webp"] + ([".avif"] if avif else [])
            notes = []
            for ext in formats:
                ok, note = _save(scaled, out_dir / f"{shot}-{target}{ext}", dry)
                written += ok
                notes.append(f"{ext.lstrip('.')} {note}")
            print(f"     {target:>5}w  " + "  ".join(notes))

        # One universal fallback at the largest useful size.
        cap = min(width, _WIDTHS[-1])
        fallback = im.resize((cap, round(height * cap / width)), Image.LANCZOS)
        ok, note = _save(fallback, out_dir / f"{shot}.jpg", dry)
        written += ok
        print(f"     {'jpg':>5}   fallback {note}")
        return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", nargs="?", help="folder of originals, one dir per model")
    ap.add_argument("dest", nargs="?", help="media directory the site serves")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    ap.add_argument("--avif", action="store_true", help="also write AVIF renditions")
    ap.add_argument("--list", action="store_true", help="show the shot names and exit")
    args = ap.parse_args()

    if args.list:
        print("Models :", ", ".join(MODELS))
        print("Shots  :", ", ".join(SHOT_NAMES))
        print("\nName each original after its shot, e.g. originals/l801/front.jpg")
        return 0
    if not args.source or not args.dest:
        ap.error("source and dest are required (or use --list)")

    source, dest = Path(args.source), Path(args.dest)
    if not source.is_dir():
        return print(f"not a directory: {source}") or 1

    total = 0
    for slug in MODELS:
        model_src = source / slug
        if not model_src.is_dir():
            print(f"\n{slug}: no originals at {model_src} — skipping")
            continue
        print(f"\n{slug} → {dest / slug}")
        for original in sorted(model_src.iterdir()):
            if original.suffix.lower() not in ORIGINAL_SUFFIXES:
                continue
            if original.stem.lower() not in SHOT_NAMES:
                print(
                    f"  {original.name}: not a known shot name — skipped "
                    f"(run --list to see them)"
                )
                continue
            total += process_shot(original, dest / slug, args.dry_run, args.avif)

    verb = "would write" if args.dry_run else "wrote"
    print(f"\n{verb} {total} file(s).")
    if args.dry_run:
        print("Re-run without --dry-run to write them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
