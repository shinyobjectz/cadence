import { chromium } from 'playwright-core'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const media = new Set()
page.on('response', (r) => {
  const u = r.url(); const ct = r.headers()['content-type'] || ''
  if (/\.(mp4|m4a|mp3|webm|m3u8)(\?|$)/i.test(u) || /audio\/|video\//i.test(ct)) media.add(u)
})
await page.goto('https://elevenlabs.io/dubbing', { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(5000)
const langs = ['English', 'Portuguese', 'German', 'Italian', 'Hindi', 'Spanish']
for (const L of langs) {
  const before = media.size
  const loc = page.locator(`text=${L}`).first()
  await loc.click({ timeout: 4000 }).catch(() => {})
  await page.waitForTimeout(3500)
  console.error(`clicked ${L}: +${media.size - before} media`)
}
await page.waitForTimeout(3000)
console.log([...media].join('\n'))
await browser.close()
