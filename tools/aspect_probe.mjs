// Probe how the target app reflows at each recording viewport, so the aspect
// presets in record.mjs are based on measured breakpoints, not guesses.
import { chromium } from 'playwright-core'
const PROFILE = process.env.HOME + '/.cache/ellua/chrome-profile'
const URL = 'https://elevenlabs.io/app/dubbing'
const SIZES = [
  ['16x9', 1600, 1000],
  ['1x1', 1120, 1120],
  ['4x5', 1000, 1250],
  ['4x5n', 900, 1125],
]
const ctx = await chromium.launchPersistentContext(PROFILE, {
  channel: 'chrome', headless: false, viewport: null,
  args: ['--run-all-compositor-stages-before-draw'],
})
for (const [name, w, h] of SIZES) {
  const p = await ctx.newPage()
  await p.setViewportSize({ width: w, height: h })
  await p.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await p.waitForTimeout(6000)
  const info = await p.evaluate(() => {
    const vw = innerWidth
    // widest element pinned to the left edge that is taller than half the page
    let side = null
    for (const el of document.querySelectorAll('div,nav,aside')) {
      const r = el.getBoundingClientRect()
      if (r.left <= 2 && r.width > 120 && r.width < 400 && r.height > innerHeight * 0.5) {
        if (!side || r.width > side.w) side = { w: Math.round(r.width), tag: el.tagName, cls: (el.className || '').toString().slice(0, 60) }
      }
    }
    const drop = [...document.querySelectorAll('*')]
      .find((e) => /drag and drop them here/i.test(e.textContent || '') && e.children.length < 4)
    const dr = drop ? drop.getBoundingClientRect() : null
    // any button that looks like a sidebar collapse toggle
    const toggles = [...document.querySelectorAll('button')]
      .filter((b) => /collaps|sidebar|menu|toggle/i.test((b.getAttribute('aria-label') || '') + (b.className || '')))
      .map((b) => ({ al: b.getAttribute('aria-label'), cls: (b.className || '').toString().slice(0, 50) }))
    return {
      vw, side, toggles: toggles.slice(0, 4),
      drop: dr ? { x: Math.round(dr.x), y: Math.round(dr.y), w: Math.round(dr.width), h: Math.round(dr.height) } : null,
      hasV2: !!document.body.textContent.match(/Dubbing v2/),
    }
  })
  console.log(name, w + 'x' + h, JSON.stringify(info))
  await p.screenshot({ path: `/tmp/aspect_${name}.png` })
  await p.close()
}
await ctx.close()
console.log('done')
