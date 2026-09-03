#!/usr/bin/env node
// ellua-record: deterministic lossless "screen recording" of a LIVE page.
// Playwright-core + system Chrome + CDP virtual time: the page's clock advances
// exactly 1/fps per captured frame, real mouse events fire per tick (so :hover,
// dropdowns, JS animation all render truthfully), every frame is a lossless PNG.
//
//   node record.mjs script.json outdir
//
// script.json:
// { "url": "...", "aspect": "4:5", "dpr": 2, "fps": 30,      // aspect OR width/height
//   "width": 1440, "height": 900,
//   "steps": [
//     { "hold": 0.5 },
//     { "move": { "sel": "text=Start dubbing" } },        // or "x"/"y" page px
//     { "move": { "x": 300, "y": 500, "dur": 0.8 } },
//     { "hover": 0.6 },                                    // dwell (hover state)
//     { "click": {} },
//     { "type": { "sel": "#input", "text": "hello", "cps": 14 } },
//     { "scroll": { "dy": 600, "dur": 1.0 } }
//   ] }
//
// Output: outdir/frames/%06d.png, outdir/cursor.json (per-frame cursor track in
// CSS px), outdir/manifest.json, outdir/capture.mp4 (lossless x264 4:4:4).

import { chromium } from 'playwright-core'
import { mkdirSync, writeFileSync } from 'fs'
import { readFile } from 'fs/promises'
import { spawnSync } from 'child_process'

const args = process.argv.slice(2).filter((a) => !a.startsWith('--'))
const flags = Object.fromEntries(process.argv.slice(2)
  .filter((a) => a.startsWith('--'))
  .map((a) => a.slice(2).split('=')))
const [scriptPath, outdir] = args
if (!scriptPath || !outdir) {
  console.error('usage: node record.mjs script.json outdir [--profile=dir]')
  process.exit(1)
}
const spec = JSON.parse(await readFile(scriptPath, 'utf8'))

// ---------- aspect presets ----------
// One recording script, any output shape. The viewport is chosen so the PAGE is
// already the target aspect — never a wide capture letterboxed into a tall frame.
// Widths are picked against measured app breakpoints: this app's sidebar is
// `hidden lg:block`, so anything under 1024 CSS px collapses it to a hamburger,
// which is exactly what square/vertical crops want. Override with --aspect=.
const ASPECTS = {
  '16:9': { width: 1600, height: 1000, out: [1920, 1080] },
  '1:1':  { width: 1000, height: 1000, out: [1080, 1080] },
  '4:5':  { width: 1000, height: 1250, out: [1080, 1350] },
  '9:16': { width: 800,  height: 1422, out: [1080, 1920] },
}
const aspectKey = flags.aspect || spec.aspect
if (aspectKey) {
  const a = ASPECTS[aspectKey]
  if (!a) {
    console.error(`unknown aspect "${aspectKey}" — known: ${Object.keys(ASPECTS).join(', ')}`)
    process.exit(1)
  }
  spec.width = spec.width || a.width
  spec.height = spec.height || a.height
  spec.aspect = aspectKey
  spec.out_w = a.out[0]
  spec.out_h = a.out[1]
  console.log(`aspect ${aspectKey}: viewport ${spec.width}x${spec.height} -> comp ${a.out[0]}x${a.out[1]}` +
    (spec.width < 1024 ? ' (sidebar collapsed)' : ' (sidebar shown)'))
}
const FPS = spec.fps || 30
const TICK = 1000 / FPS
mkdirSync(`${outdir}/frames`, { recursive: true })

// --profile=dir (or spec.profile): reuse an authenticated session from login.mjs
const profileDir = flags.profile || spec.profile
let browser, context
const HEADFUL = flags.headed === '' || flags.headed === 'true' || spec.headed
const LAUNCH_ARGS = ['--run-all-compositor-stages-before-draw',
  '--disable-new-content-rendering-timeout']
if (profileDir) {
  context = await chromium.launchPersistentContext(profileDir, {
    channel: 'chrome', headless: HEADFUL ? false : true, args: LAUNCH_ARGS,
    viewport: { width: spec.width || 1440, height: spec.height || 900 },
    deviceScaleFactor: spec.dpr || 2,
  })
  browser = context.browser() || context
} else {
  browser = await chromium.launch({ channel: 'chrome', headless: HEADFUL ? false : true, args: LAUNCH_ARGS })
  context = await browser.newContext({
    viewport: { width: spec.width || 1440, height: spec.height || 900 },
    deviceScaleFactor: spec.dpr || 2,
  })
}
const page = context.pages()[0] || (await context.newPage())
// networkidle never fires on SPAs with live sockets — load + fixed settle
await page.goto(spec.url, { waitUntil: 'load', timeout: 45000 })
await page.waitForTimeout(spec.settle_ms || 2500) // app/fonts settle before we own the clock

const cdp = await context.newCDPSession(page)
let frame = 0
const cursorTrack = []
let cur = { x: (spec.width || 1440) / 2, y: (spec.height || 900) / 3, down: false }

// watchdog: any single tick (virtual-time grant + screenshot) exceeding this
// aborts loudly instead of hanging a demo recording forever
const TICK_TIMEOUT_MS = Number(flags['tick-timeout'] || 20000)
let stepLabel = 'init'

function watchdog(promise, what) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() =>
      rej(new Error(`watchdog: ${what} stuck >${TICK_TIMEOUT_MS}ms during step "${stepLabel}" at frame ${frame}`)),
      TICK_TIMEOUT_MS)),
  ])
}

async function tick(mouseMove) {
  let mousePending = null
  if (mouseMove) {
    cur.x = mouseMove.x
    cur.y = mouseMove.y
    // do NOT await before granting time: under paused virtual time the page
    // can't process input, so awaiting here deadlocks. Fire, advance, collect.
    mousePending = cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseMoved', x: cur.x, y: cur.y,
      buttons: cur.down ? 1 : 0,
    })
  }
  const MICRO = 1 // ms granted during screenshot so the compositor can produce a frame
  const expired = new Promise((res) => cdp.once('Emulation.virtualTimeBudgetExpired', res))
  await watchdog(cdp.send('Emulation.setVirtualTimePolicy', { policy: 'advance', budget: TICK - MICRO })
    .then(() => expired), 'virtual time advance')
  if (mousePending) await watchdog(mousePending, 'mouse dispatch settle')
  // screenshot needs compositor work, compositor needs clock: request the shot,
  // grant a micro-budget while it's in flight, then collect
  const shotP = cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true })
  const microExpired = new Promise((res) => cdp.once('Emulation.virtualTimeBudgetExpired', res))
  await watchdog(cdp.send('Emulation.setVirtualTimePolicy', { policy: 'advance', budget: MICRO })
    .then(() => microExpired), 'micro budget')
  const shot = await watchdog(shotP, 'screenshot')
  writeFileSync(`${outdir}/frames/${String(frame).padStart(6, '0')}.png`,
    Buffer.from(shot.data, 'base64'))
  cursorTrack.push({ f: frame, x: cur.x, y: cur.y, down: cur.down })
  frame++
  if (frame % 30 === 0) console.log(`FRAME ${frame} (${(frame / FPS).toFixed(1)}s) step="${stepLabel}"`)
}

// freeze the clock: from here, time only moves when we grant it
await cdp.send('Emulation.setVirtualTimePolicy', { policy: 'pause' })

async function resolvePoint(m) {
  if (m.sel) {
    let box = await page.locator(m.sel).first().boundingBox({ timeout: 4000 }).catch(() => null)
    if (!box) {
      // text= often matches a hidden native <option> behind custom dropdowns —
      // retry constrained to the visible element
      box = await page.locator(`${m.sel} >> visible=true`).first()
        .boundingBox({ timeout: 4000 }).catch(() => null)
    }
    if (!box) throw new Error('selector not found: ' + m.sel)
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  }
  return { x: m.x, y: m.y }
}

// any failure: save a diagnostic snapshot + partial manifest, exit non-zero
process.on('uncaughtException', async (err) => {
  console.error('RECORD FAILED:', err.message)
  try {
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(`${outdir}/failure.png`, Buffer.from(shot.data, 'base64'))
    writeFileSync(`${outdir}/manifest.json`, JSON.stringify({
      error: err.message, step: stepLabel, frames: frame, fps: FPS,
    }))
    console.error(`diagnostics: ${outdir}/failure.png, ${frame} frames kept`)
  } catch {}
  process.exit(1)
})

const easeOut = (k) => 1 - Math.pow(1 - k, 3)

const total = spec.steps?.length || 0
const markers = []
let stepNum = 0
for (const step of spec.steps || []) {
  stepNum++
  const verb = Object.keys(step).find((k) => k !== 'marker') || 'step'
  stepLabel = `${stepNum}/${total} ${step.marker || verb}`
  markers.push({ n: stepNum, kind: verb, f: frame, t: frame / FPS,
    name: step.marker || null })
  console.log(`STEP ${stepLabel} (frame ${frame})`)
  if (step.hold != null) {
    const n = Math.round(step.hold * FPS)
    for (let i = 0; i < n; i++) await tick()
  } else if (step.hover != null) {
    const n = Math.round(step.hover * FPS)
    for (let i = 0; i < n; i++) await tick({ ...cur })
  } else if (step.move) {
    const to = await resolvePoint(step.move)
    const dist = Math.hypot(to.x - cur.x, to.y - cur.y)
    const dur = step.move.dur || Math.min(1.1, 0.25 + dist / 1200)
    const n = Math.max(2, Math.round(dur * FPS))
    const from = { ...cur }
    const bow = Math.min(60, dist * 0.18)
    const mx = (from.x + to.x) / 2 - ((to.y - from.y) / (dist || 1)) * bow
    const my = (from.y + to.y) / 2 + ((to.x - from.x) / (dist || 1)) * bow
    for (let i = 1; i <= n; i++) {
      const k = easeOut(i / n)
      const a = { x: from.x + (mx - from.x) * k, y: from.y + (my - from.y) * k }
      const b = { x: mx + (to.x - mx) * k, y: my + (to.y - my) * k }
      await tick({ x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k })
    }
  } else if (step.click) {
    const press = cdp.send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: cur.x, y: cur.y, button: 'left', clickCount: 1, buttons: 1,
    })
    cur.down = true
    for (let i = 0; i < Math.round(0.1 * FPS); i++) await tick()
    await watchdog(press, 'mouse press settle')
    const release = cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: cur.x, y: cur.y, button: 'left', clickCount: 1, buttons: 0,
    })
    cur.down = false
    for (let i = 0; i < Math.round((step.click.settle ?? 0.4) * FPS); i++) await tick()
    await watchdog(release, 'mouse release settle')
  } else if (step.type) {
    const loc = page.locator(step.type.sel).first()
    await loc.click({ timeout: 5000 })
    const perChar = Math.max(1, Math.round(FPS / (step.type.cps || 12)))
    for (const ch of step.type.text) {
      await page.keyboard.type(ch)
      for (let i = 0; i < perChar; i++) await tick()
    }
  } else if (step.upload) {
    // setInputFiles is programmatic — it never moves the mouse. Left alone, the
    // recorded cursor sits at its idle spot during the upload frame, and any
    // comp that lands a synthesized drag on track_at(upload) drops it in the
    // wrong place. `zone` makes the recorder walk the cursor onto the real drop
    // target first, so the track tells the truth about where the file landed.
    const zone = step.upload.zone
    // the marker is stamped when the step BEGINS; for an upload with an
    // approach that is the start of the walk, not the drop. Re-stamp it at the
    // instant the file actually lands so `mark("upload")` means what it says.
    const upMarker = markers[markers.length - 1]
    if (zone) {
      const to = await resolvePoint({ sel: zone })
      const dist = Math.hypot(to.x - cur.x, to.y - cur.y)
      const n = Math.max(2, Math.round((step.upload.approach ?? Math.min(0.9, 0.25 + dist / 1200)) * FPS))
      const from = { ...cur }
      for (let i = 1; i <= n; i++) {
        const k = easeOut(i / n)
        await tick({ x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k })
      }
    }
    upMarker.f = frame
    upMarker.t = frame / FPS
    const sel = step.upload.sel || 'input[type=file]'
    await watchdog(page.setInputFiles(sel, step.upload.file, { timeout: 8000 }), 'setInputFiles')
    for (let i = 0; i < Math.round((step.upload.settle ?? 1.0) * FPS); i++) await tick()
  } else if (step.waitfor) {
    // real-clock bounded wait for an element while the page clock keeps
    // advancing (so spinners/progress UI animate); capture at reduced rate
    const deadline = Date.now() + (step.waitfor.timeout_s || 120) * 1000
    const loc = page.locator(step.waitfor.sel).first()
    let found = false
    let n = 0
    while (Date.now() < deadline) {
      await tick()
      n++
      if (n % 5 === 0) {
        const count = await watchdog(loc.count(), 'waitfor probe')
        if (count > 0) { found = true; break }
      }
    }
    if (!found) throw new Error(`waitfor timed out: ${step.waitfor.sel}`)
    for (let i = 0; i < Math.round((step.waitfor.settle ?? 0.5) * FPS); i++) await tick()
  } else if (step.select) {
    // native <select>: OS-rendered popups can't be clicked in-page — use the
    // select API (nth picks among multiple anonymous selects)
    const loc = page.locator('select').nth(step.select.nth ?? 0)
    const p = loc.selectOption(step.select.value)
      .then(() => {}, (e) => { throw e })
    for (let i = 0; i < Math.round((step.select.settle ?? 0.8) * FPS); i++) await tick()
    await watchdog(p, 'selectOption settle')
    for (let i = 0; i < Math.round((step.select.after ?? 0.4) * FPS); i++) await tick()
  } else if (step.pwclick) {
    // Playwright-actionability click for stubborn custom widgets. Its internal
    // stability checks ride rAF, which only advances with granted budget — so
    // keep ticking until the click actually resolves (bounded), then settle.
    let done = false
    let failed = null
    const p = page.locator(`${step.pwclick.sel} >> visible=true`).first()
      .click({ timeout: 15000, force: step.pwclick.force || false })
      .then(() => { done = true }, (e) => { done = true; failed = e })
    let guard = 0
    while (!done && guard < 20 * FPS) {
      await tick()
      guard++
    }
    await p
    if (failed) throw failed
    for (let i = 0; i < Math.round((step.pwclick.settle ?? 0.6) * FPS); i++) await tick()
  } else if (step.press) {
    const key = step.press.key || step.press
    const p = page.keyboard.press(key)
    for (let i = 0; i < Math.round((step.press.settle ?? 0.3) * FPS); i++) await tick()
    await watchdog(p, 'keypress settle')
  } else if (step.scroll) {
    const n = Math.max(2, Math.round((step.scroll.dur || 1.0) * FPS))
    let done = 0
    for (let i = 1; i <= n; i++) {
      const target = Math.round(step.scroll.dy * easeOut(i / n))
      const delta = target - done
      done = target
      let wheel = null
      if (delta !== 0) {
        wheel = cdp.send('Input.dispatchMouseEvent', {
          type: 'mouseWheel', x: cur.x, y: cur.y, deltaX: 0, deltaY: delta,
        })
      }
      await tick()
      if (wheel) await watchdog(wheel, 'wheel settle')
    }
  }
}

writeFileSync(`${outdir}/cursor.json`, JSON.stringify(cursorTrack))
writeFileSync(`${outdir}/markers.json`, JSON.stringify(markers, null, 1))
const manifest = {
  url: spec.url, fps: FPS, width: spec.width || 1440, height: spec.height || 900,
  dpr: spec.dpr || 2, frames: frame,
  aspect: spec.aspect || null, out_w: spec.out_w || null, out_h: spec.out_h || null,
}
writeFileSync(`${outdir}/manifest.json`, JSON.stringify(manifest))

// Lua sidecars: a comp does `dofile(rec .. "/meta.lua")` with no JSON step and
// no hand-conversion. meta carries the viewport so demo.live can self-fit.
const luaVal = (v) => v === null ? 'nil' : typeof v === 'string' ? JSON.stringify(v) : String(v)
const luaTable = (o) => '{ ' + Object.entries(o)
  .map(([k, v]) => `${k} = ${luaVal(v)}`).join(', ') + ' }'
writeFileSync(`${outdir}/meta.lua`, 'return ' + luaTable(manifest) + '\n')
writeFileSync(`${outdir}/cursor.lua`, 'return {\n' + cursorTrack
  .map((c) => `{f=${c.f},x=${c.x.toFixed(1)},y=${c.y.toFixed(1)},down=${!!c.down}}`)
  .join(',\n') + '\n}\n')
writeFileSync(`${outdir}/markers.lua`, 'return {\n' + markers
  .map((m) => `{n=${m.n},kind=${JSON.stringify(m.kind)},f=${m.f},t=${m.t.toFixed(4)},name=${luaVal(m.name)}}`)
  .join(',\n') + '\n}\n')
await context.close()
if (!profileDir) await browser.close()

// lossless encode (x264 qp0 4:4:4 — decode-crate falls to its RGBA path for 444)
const enc = spawnSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
  '-framerate', String(FPS), '-i', `${outdir}/frames/%06d.png`,
  '-c:v', 'libx264', '-qp', '0', '-preset', 'veryfast', '-pix_fmt', 'yuv444p',
  `${outdir}/capture.mp4`])
if (enc.status !== 0) {
  console.error('ffmpeg encode failed:', enc.stderr?.toString())
  process.exit(1)
}
console.log(`recorded ${frame} frames @${FPS}fps -> ${outdir}/capture.mp4`)
