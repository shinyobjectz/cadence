# Ellua renderer eval

Public visual suite for the headless renderer. The compositions are original
fixtures. The media they decode is from Wikimedia Commons (NASA public domain
video/image/audio, CC SVGs) and an Apache-2.0 Lottie sample. This is not
product-demo content from other projects.

```bash
cd ellua
bin/eval --open
```

That command:

1. Fetches missing files listed in `evals/assets.json`
2. Renders every case in `evals/manifest.json` through the headless renderer
3. Overwrites `evals/out/eval.html` with every passing MP4 inlined as a data URI

| path | purpose |
|---|---|
| `evals/cases/` | original compositions covering primitives, type, compositing, decode, encode |
| `evals/assets.json` | URLs, licenses, and credits for downloaded media |
| `evals/assets/` | local copies (fetched on demand) |
| `evals/out/` | render artifacts + `eval.html`; gitignored |

Media cases cover WebM video, JPEG, SVG, Lottie, and Ogg audio mix. Geometry
cases cover shapes, eases, kinetic type, blend modes, clip masks, vector,
effects, HTML fragments, HTML pages (linked CSS/images/woff2), script-baked
pages, flex, exploded UI perspective, focus-pull perspective, a shared plane
camera, and a glTF world layer.
