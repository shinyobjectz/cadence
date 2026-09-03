# Brand capture — real fonts, colors, components, logos from a URL

## Tool

```
bin/ellua-capture <url> <outdir>
```

Blitz page paint (`ellua-html-shot`) + curl + woff2. No Chrome. Outputs:

| file | contents | use in comps |
|------|----------|--------------|
| `fonts/*.ttf` | every `@font-face` woff2, decompressed to TTF | `s:text{ font = "<outdir>/fonts/X.ttf" }`, `s:kinetic{ font = ... }` — LÖVE loads TTF/OTF only, never woff2 |
| `brand.json` | `css_color_vars` (custom props matching color/bg/accent/primary), `hex_by_frequency` (top hexes across CSS+DOM), `font_families` | pick ink/paper/accent from the top entries; verify against `page.png` before trusting frequency alone |
| `page.png` | viewport screenshot (1440×3000) from Blitz after script bake | crop regions with ffmpeg (`crop=W:H:X:Y`) → `s:image` product cards; or `s:page{ src = "<outdir>/dom.html", base = <url> }` |
| `dom.html` | fetched source HTML | `s:page{ src= }` bakes scripts at resolve; or copy a component into `s:html{}` |
| `styles/*.css` | downloaded stylesheets + inline `<style>` blocks | component CSS for `s:html`; grep for exact tokens |
| `logos/` | favicon, og:image, any `src/href` matching `logo`, first inline `<svg …logo…>` | `s:svg` (svg) / `s:image` (png) |

Requires `bin/build-native` so `native/release/ellua-html-shot` exists.

## Node props this feeds

- `s:text{ font = "path.ttf" }` / `s:kinetic{ font = "path.ttf" }` — path absolute
  or relative to the invoking cwd. Kinetic char measuring uses the same font, so
  spacing is correct per-face. Fonts cache per (path, size).
- `s:page{ src = "path.html" }` — Blitz fetches linked CSS/images/fonts at
  resolve and paints a viewport. woff2 `@font-face` is decoded for Blitz.
  Classic scripts bake once (tiny document) unless `bake=false`.
- `s:html{ html = <captured markup + css> }` — fragment rung: inline CSS
  snippets, no network. Flexbox, gradients, border-radius, box-shadow.
- `s:text{ font = "path.ttf" }` still uses capture's `fonts/*.ttf` (LÖVE cannot
  load woff2). Page paint uses the same files through Blitz.
- `s:image` / `s:svg` for logos and page crops.

## Limits (facts, not warnings)

- Classic scripts bake at resolve (`document.getElementById`, `textContent`,
  `classList`, `querySelector`). React/Vue SPAs and `type=module` are not a
  browser. Pass `ellua-html-shot --no-bake` to paint source HTML.
- `hex_by_frequency` counts CSS occurrences, not visual area — a shadow color can
  outrank the brand accent. Cross-check `page.png`.
- Inline-`<svg>` logo extraction takes the first match containing "logo"; sprite
  sheets and CSS-background logos are not extracted.
- Font downloads take the first 8 woff2 urls; icon fonts may be among them —
  check filenames.
- Screenshot viewport is fixed 1440×3000.
