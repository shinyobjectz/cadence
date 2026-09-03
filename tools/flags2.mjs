import { chromium } from 'playwright-core'
import { homedir } from 'os'
const ctx = await chromium.launchPersistentContext(homedir() + '/.cache/ellua/chrome-profile', {
  channel: 'chrome', headless: true, viewport: { width: 1920, height: 1080 },
})
const page = ctx.pages()[0] || (await ctx.newPage())
await page.goto('https://elevenlabs.io/app/dubbing', { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(7000)
const out = await page.evaluate(() => {
  const res = { flags: null, dubFlags: [] }
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (!/posthog/i.test(k)) continue
    try {
      const o = JSON.parse(localStorage.getItem(k))
      const f = o.$enabled_feature_flags || o.$feature_flags || null
      if (f) {
        res.flags = f
        res.dubFlags = Object.keys(f).filter((x) => /dub|v2|alpha/i.test(x)).map((x) => `${x} = ${f[x]}`)
      }
    } catch {}
  }
  return res
})
console.log('dub-related flags:', JSON.stringify(out.dubFlags, null, 1))
console.log('total flags:', out.flags ? Object.keys(out.flags).length : 0)
if (out.flags) console.log('all keys:', Object.keys(out.flags).slice(0, 40).join(', '))
await ctx.close()
