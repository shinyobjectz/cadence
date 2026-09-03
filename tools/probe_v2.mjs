import { chromium } from 'playwright-core'
import { homedir } from 'os'
const ctx = await chromium.launchPersistentContext(homedir() + '/.cache/ellua/chrome-profile', {
  channel: 'chrome', headless: false, viewport: { width: 1600, height: 1000 },
})
const page = ctx.pages()[0] || (await ctx.newPage())
await page.goto('https://elevenlabs.io/app/dubbing', { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(7000)
await page.setInputFiles('input[type=file]',
  '/Users/shinyobjectz/11l/ellua/examples/assets/el_demos/dp_EN.mp4').catch((e) => console.log('upload:', e.message))
await page.waitForTimeout(4000)
await page.locator('text=Choose languages').first().click().catch((e) => console.log('choose:', e.message))
await page.waitForTimeout(2500)
const info = await page.evaluate(() => {
  const btns = [...document.querySelectorAll('button')].map((b) => ({
    t: (b.innerText || '').trim().slice(0, 26),
    al: b.getAttribute('aria-label'),
    cls: (b.className || '').toString().slice(0, 40),
    r: b.getBoundingClientRect(),
  })).filter((b) => b.r.width > 0)
  return {
    submitish: btns.filter((b) => !b.t && b.r.width < 70 && b.r.width > 20)
      .map((b) => `[icon] al=${b.al} ${Math.round(b.r.x)},${Math.round(b.r.y)} ${Math.round(b.r.width)}px cls=${b.cls}`),
    labeled: btns.filter((b) => b.t).slice(0, 22).map((b) => `"${b.t}" ${Math.round(b.r.x)},${Math.round(b.r.y)}`),
  }
})
console.log('ICON BUTTONS:\n ' + info.submitish.join('\n '))
console.log('\nLABELED:\n ' + info.labeled.join('\n '))
await page.screenshot({ path: '/tmp/v2_probe.png' })
await ctx.close()
