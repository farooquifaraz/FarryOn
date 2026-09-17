# Product photography and video

The glasses cost AED 300–350. Until the shoot lands, the spec cards on the
landing page show a 🕶 emoji where the product should be. This describes how to
get real photographs onto those cards.

**Nothing here is required for the site to work.** With no media on disk the
page renders exactly as it always has — no gallery, no stylesheet, no script.
Drop files in and the galleries appear; delete them and they are gone again.

---

## 1. What to shoot

Per model (`L801 Business`, `L802 Premium`). Everything is optional; a card
shows whatever exists, in this order:

| File name     | The shot                                        | Priority |
| ------------- | ----------------------------------------------- | -------- |
| `front`       | Straight on, plain background                   | ★ must   |
| `angle`       | Three-quarter, showing temple and hinge         | ★ must   |
| `camera`      | Macro of the camera module in the frame         | ★ must   |
| `folded`      | Folded, showing how slim it sits                | nice     |
| `worn`        | On a face, front                                | ★ must   |
| `worn-side`   | On a face, side                                 | nice     |
| `lifestyle`   | In real use                                     | nice     |
| `scale`       | In a hand, for size and weight                  | nice     |
| `box`         | What is in the box                              | nice     |
| `video`       | 30–40s: the product, then someone using it      | ★ strong |

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
is unset. Two workable choices:

**Commit them** (simplest). Optimized, a full set for both models is roughly
1–2MB — fine in git, and deploys with the code. Nothing more to configure.

**Mount a volume** (better once video is involved). Set `MEDIA_DIR` to a
directory outside the image and drop files in without a redeploy:

```yaml
# docker-compose.prod.yml
environment:
  MEDIA_DIR: /srv/media
volumes:
  - /srv/farryon/media:/srv/media:ro
```

Either way the app serves the files itself at `/media/{model}/{file}`. That is
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

## 5. Adding a third model

`backend/app/web/products.py` holds the model list:

```python
MODELS = {"l801": "L801 Business", "l802": "L802 Premium"}
```

Add the slug there, add a `<!--PRODUCT_MEDIA:slug-->` slot inside that card's
`<div class="spec-inner">` in `index.html`, and create `media/<slug>/`. No
other change — the gallery, the route and the optimizer all read that one dict.
