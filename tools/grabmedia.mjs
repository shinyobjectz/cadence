import { chromium } from 'playwright-core'
const url = process.argv[2] || 'https://elevenlabs.io/dubbing'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const media = new Set()
page.on('response', (r) => {
  const u = r.url()
  const ct = r.headers()['content-type'] || ''
  if (/\.(mp4|m3u8|webm|mov|mpd)(\?|$)/i.test(u) || /video\/|application\/vnd\.apple\.mpegurl|dash/i.test(ct)) {
    media.add(`${ct.split(';')[0].padEnd(28)} ${u}`)
  }
})
await page.goto(url, { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(6000)
// click any play buttons / language pills to trigger more loads
for (const sel of ['button[aria-label*="lay" i]', 'video', '[class*="play" i]']) {
  const els = await page.locator(sel).all().catch(() => [])
  for (const e of els.slice(0, 3)) { await e.click({ timeout: 1500 }).catch(() => {}) }
}
await page.waitForTimeout(6000)
// also read <video>/<source> tags in DOM
const dom = await page.evaluate(() =>
  [...document.querySelectorAll('video, source')].map((v) => v.src || v.currentSrc).filter(Boolean))
dom.forEach((u) => media.add(`DOM                          ${u}`))
console.log([...media].join('\n') || '(no media found)')
await browser.close()
