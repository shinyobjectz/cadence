import { chromium } from 'playwright-core'
import { homedir } from 'os'
const url = process.argv[2]
const out = process.argv[3] || '/tmp/peek.png'
const ctx = await chromium.launchPersistentContext(homedir() + '/.cache/ellua/chrome-profile', {
  channel: 'chrome', headless: true,
  viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2,
})
const page = ctx.pages()[0] || (await ctx.newPage())
try { await page.goto(url, { waitUntil: 'load', timeout: 45000 }) } catch (e) { console.log('nav:', e.message) }
await page.waitForTimeout(6000)
console.log('URL:', page.url())
await page.screenshot({ path: out })
await ctx.close()
