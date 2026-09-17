# Product photography, video and catalogs

The glasses cost AED 300–450. This describes how photographs and the product
catalog PDFs get onto the spec cards — and where they live on the server.

**Current state (2026-09-17):** all four models (L801, L802, Farry-GS4,
GS5 MAX) have a photo set built from the factory's studio PNGs, and a catalog
PDF each. They are on the VPS at `/opt/farryon-media/` (see §3), not in git.

**Nothing here is required for the site to work.** With no media on disk the
page renders exactly as it always has — no gallery, no stylesheet, no script.
Drop files in and the galleries appear; delete them and they are gone again.

---

## 1. What to shoot

Per model (`l801`, `l802`, `gs4`, `gs5`). Everything is optional; a card
shows whatever exists, in this order:

| File name     | The shot                                        | Priority |
| ------------- | ----------------------------------------------- | -------- |
| `front`       | Straight on, plain background                   | ★ must   |
| `angle`       | Three-quarter, showing temple and hinge         | ★ must   |
| `camera`      | Macro of the camera module in the frame         | ★ must   |
| `lenses`      | With the clear anti-blue lenses fitted          | nice     |
| `side`        | From the side (temple, speaker)                 | nice     |
| `back`        | From behind (inside of the temples)             | nice     |
| `top`         | From above                                      | nice     |
| `folded`      | Folded, showing how slim it sits                | nice     |
| `worn`        | On a face, front                                | ★ must   |
| `worn-side`   | On a face, side                                 | nice     |
| `red`, `cream`| Colour variants (GS5 MAX)                       | nice     |
| `case`        | The charging case (GS5 MAX)                     | nice     |
| `charging`    | Glasses charging in the case                    | nice     |
| `lifestyle`   | In real use                                     | nice     |
| `scale`       | In a hand, for size and weight                  | nice     |
| `box`         | What is in the box                              | nice     |
| `video`       | 30–40s: the product, then someone using it      | ★ strong |

**Transparent PNGs are best.** The factory's studio cut-outs keep their
transparency in WebP/AVIF and sit on the card's own dark gradient; the JPEG
fallback is flattened onto that same dark colour. A white-background JPEG
would show as a white box on the dark page — knock the background out first
(the 2026-09 L802 set was prepared that way: near-white to transparent, then
cropped and centred on a 3:2 canvas).

Shoot at least 2000px wide. Landscape, roughly 3:2 — the stage is 3:2 on
desktop and 4:3 on phones, and the photo is fitted inside without cropping, so
an odd shape just gets letterboxed rather than cut.

### Two warnings worth the money

1. **Do not use the supplier's stock photos.** If these frames are white-label,
   the same images are on every competitor's site and one reverse-image search
   finds them. That is the fastest way to lose a buyer who is already unsure
   about an unfamiliar brand.
2. **Shoot Indian models too** if the India market goes ahead. A UAE-only
   lifestyle set reads as "not for me" to an Indian buyer, and the `worn` shots
   are the ones that sell.

---

## 2. Prepare the files

Put the originals in one folder per model, each named after its shot:

```
originals/
  l801/front.jpg  l801/angle.jpg  l801/camera.jpg  ...
  l802/front.jpg  ...
```

Then, from `backend/`:

```bash
python scripts/optimize_media.py ../originals app/web/media --dry-run   # look first
python scripts/optimize_media.py ../originals app/web/media             # write
```

That produces, for every shot, four WebP widths plus one JPEG fallback:

```
front-400.webp  front-800.webp  front-1200.webp  front-1600.webp  front.jpg
```

The page hands all of them to the browser and it picks one. In practice a
2.3MB studio JPEG becomes the ~50KB file a phone actually downloads.

`--avif` also writes AVIF (smaller again) when Pillow is built with libavif;
without it the run just says so and carries on. `python scripts/optimize_media.py
--list` prints the shot names it accepts — anything else is reported and skipped
rather than written somewhere nothing will render it.

### Video

The optimizer does not touch video; re-encoding it needs ffmpeg:

```bash
ffmpeg -i raw.mov -vf scale=1280:-2 -c:v libx264 -crf 23 -preset slow \
       -c:a aac -b:a 128k -movflags +faststart app/web/media/l801/video.mp4
ffmpeg -i raw.mov -vf scale=1280:-2 -c:v libvpx-vp9 -crf 33 -b:v 0 \
       -c:a libopus app/web/media/l801/video.webm
ffmpeg -i raw.mov -ss 2 -frames:v 1 app/web/media/l801/video-poster.jpg
```

`video.mp4` alone is enough — `.webm` is a smaller optional extra, and the
poster is what visitors see until they press play. Keep the file under ~10MB.
Nothing pre-loads: the poster is all that downloads until someone clicks.

---

## 3. Where the files live in production

The gallery reads `MEDIA_DIR`, falling back to `backend/app/web/media/` when it
is unset. Production mounts a host directory (`docker-compose.prod.yml`):

```yaml
environment:
  MEDIA_DIR: /srv/media
volumes:
  - ${MEDIA_HOST_DIR:-/opt/farryon-media}:/srv/media:ro
```

So on the VPS the files are at **`/opt/farryon-media/`** — deliberately
*outside* `/opt/farryon`, which the deploy rsyncs with `--delete` (anything in
there that is not in git is erased on every deploy; `/opt/farryon-builds` is
kept outside for the same reason). Layout on the host:

```
/opt/farryon-media/
  l801/ l802/ gs4/ gs5/      # the optimizer's output, one dir per model
  catalogs/l801.pdf ...      # one PDF per model, served at /catalogs/<slug>.pdf
```

To publish a new set from a PC:

```bash
scp -r media/l801 media/catalogs root@72.60.103.157:/opt/farryon-media/
```

No restart needed: the scan is keyed on the directory's mtime, so a new file
is on the next page load. Nothing goes in the GitHub secret for this — the
container path is set in compose.

Both `/media/*` and `/catalogs/*` are in Caddy's forward list
(`admin/Caddyfile`); a path missing there is answered by the admin SPA's
`index.html`, not a 404, which is why the tests check the Caddyfile too.

The app serves the files itself at `/media/{model}/{file}`. That is
fine at current traffic; if the galleries ever become the bulk of the bandwidth
bill, put a CDN (Cloudflare, Bunny) in front of `/media` — no code change, the
cache headers are already right.

> **Replacing a photo:** files are cached in visitors' browsers for a day. Save
> a better shot over an existing name and returning visitors may see the old one
> until that expires. To publish immediately, use a new name (`front-v2.jpg` is
> *not* a shot name — re-run the optimizer so the whole set is rewritten), or
> just accept the day. A first-time visitor always gets the new file.

---

## 4. Checking it worked

```bash
cd backend && python -m pytest tests/test_site_product_media.py -q
```

Then load the site and scroll to **Glasses**. You should see, per model with
media: a large image, arrows, a thumbnail strip, and a click-to-enlarge
lightbox. Arrow keys and Escape work; on a phone, so does swiping.

If a card shows nothing, the filename is almost certainly not one of the names
in the table above — `--list` prints the accepted set.

---

## 5. Catalogs

One PDF per model at `<media root>/catalogs/<slug>.pdf`. When it exists the
card gets a "Product catalog · PDF · 4.2 MB" link (`/catalogs/<slug>.pdf`,
served inline so it opens in the browser and forwards on WhatsApp in one
share); when it does not, no link. The 2026-09 PDFs are the supplier decks
with their placeholder "Get in Touch" page replaced by a real one (FarryOn,
website, WhatsApp, email) — `[Company name]` must never reach a reseller.

## 6. Adding a fifth model

`backend/app/web/products.py` holds the model list:

```python
MODELS = {"l801": "L801 Business", "l802": "L802 Premium", "gs4": "Farry-GS4", "gs5": "GS5 MAX"}
```

Add the slug there, add `<!--PRODUCT_MEDIA:slug-->`, `<!--WHATSAPP_CARD:slug-->`
and `<!--CATALOG:slug-->` slots inside that card's `<div class="spec-inner">`
in `index.html`, a pre-filled message in `contact.MESSAGES`, and create
`media/<slug>/`. The gallery, the routes and the optimizer all read that one dict.
