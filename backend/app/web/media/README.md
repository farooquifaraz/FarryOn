# Product media

Photographs and video for the glasses galleries on the landing page. Empty by
default — the spec cards simply render without a gallery until files appear.

```
media/
  l801/  front.jpg  front-400.webp … front-1600.webp  camera.jpg …  video.mp4
  l802/  …
```

Do not name files by hand. Put the originals in `originals/<model>/<shot>.jpg`
and run, from the repo root:

    make media-check   # see what would be written
    make media         # write it

`docs/MEDIA.md` has the shot list, the ffmpeg commands for video, and how to
serve this from a mounted volume instead of the repo in production.
