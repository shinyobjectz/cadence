import { chromium } from 'playwright-core'
import { homedir } from 'os'
const W = Number(process.argv[2] || 1920), H = Number(process.argv[3] || 1080)
const out = process.argv[4] || '/tmp/peek2.png'
const ctx = await chromium.launchPersistentContext(homedir() + '/.cache/ellua/chrome-profile', {
  channel: 'chrome', headless: true,
  viewport: { width: W, height: H }, deviceScaleFactor: 1,
})
const page = ctx.pages()[0] || (await ctx.newPage())
await page.goto('https://elevenlabs.io/app/dubbing', { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(7000)
const has = await page.evaluate(() => ({
  dragdrop: !!document.body.innerText.match(/drag and drop them here/i),
  createBtn: !!document.body.innerText.match(/Create a Dub/i),
  v2: !!document.body.innerText.match(/Dubbing v2/i),
  chooseLang: !!document.body.innerText.match(/Choose languages/i),
}))
console.log(`${W}x${H}`, JSON.stringify(has))
await page.screenshot({ path: out })
await ctx.close()
