import { chromium } from 'playwright-core'
import { homedir } from 'os'
const ctx = await chromium.launchPersistentContext(homedir() + '/.cache/ellua/chrome-profile', {
  channel: 'chrome', headless: true, viewport: { width: 1920, height: 1080 },
})
const page = ctx.pages()[0] || (await ctx.newPage())
await page.goto('https://elevenlabs.io/app/dubbing', { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(6000)
const info = await page.evaluate(() => {
  const ls = {}
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    const v = localStorage.getItem(k) || ''
    if (/flag|feature|dub|v2|alpha|experiment|ab_|variant/i.test(k + v)) ls[k] = v.slice(0, 180)
  }
  return { keys: Object.keys(localStorage).length, matched: ls, cookies: document.cookie.slice(0, 300) }
})
console.log(JSON.stringify(info, null, 1))
await ctx.close()
