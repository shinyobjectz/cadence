# Ellua LÖVE renderer fork

This directory vendors LÖVE from `love2d/love` at commit
`f58418aa4e83e434939323c37376879c2344813f` (the upstream `main` branch when
vendored on 2026-08-13).
Its matching dependency source is vendored in `megasource/` from
`love2d/megasource` at commit
`5118c13ca1dea1890e298c0ce5e410846913a5a8`.

Ellua keeps the public LÖVE-compatible Lua API. The only intentional runtime
change is an opt-in offline-rendering mode:

```text
ELLUA_HEADLESS=1 ellua-love runtime --render composition.lua
```

With `ELLUA_HEADLESS=1`, the SDL window module creates no `SDL_Window` and
passes a headless presentation mode to the graphics backend. The Metal backend
renders into an engine-owned virtual backbuffer and commits command buffers
without a drawable or swapchain presentation. Preview mode must not set
`ELLUA_HEADLESS`.

Vulkan headless support is not complete yet: it must use its virtual
backbuffer path without creating an SDL surface or swapchain before a
Linux/Windows release can be made.

`license.txt` is the upstream zlib-style LÖVE license and must remain in every
source and binary distribution. This source tree is deliberately vendored
rather than treated as a Git submodule so an Ellua release can be built from a
fully pinned source checkout.

## Patch policy

- Keep Ellua-specific changes minimal and documented here.
- Port the patch forward when updating the upstream revision.
- Do not change public `love.*` semantics for compositions.
- Configure all platform builds through `megasource/` with
  `-DMEGA_LOVE=<this directory>` so source dependencies are pinned as well.
- Build products belong outside this directory (under `build/` or `dist/`) and
  are not source-controlled.
