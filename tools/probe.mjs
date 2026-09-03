import { chromium } from 'playwright-core'
import { homedir } from 'os'
const ctx = await chromium.launchPersistentContext(homedir() + '/.cache/ellua/chrome-profile', {
  channel: 'chrome', headless: true, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
const page = ctx.pages()[0] || await ctx.newPage()
try {
  await page.goto('https://elevenlabs.io/app/dubbing', { waitUntil: 'networkidle', timeout: 45000 })
} catch (e) { console.log('nav:', e.message) }
console.log('URL:', page.url())
await page.screenshot({ path: '/tmp/app_probe.png' })
await ctx.close()
