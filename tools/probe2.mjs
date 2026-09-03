import { chromium } from 'playwright-core'
import { homedir } from 'os'
const ctx = await chromium.launchPersistentContext(homedir() + '/.cache/ellua/chrome-profile', {
  channel: 'chrome', headless: true, viewport: { width: 1440, height: 900 } })
const page = ctx.pages()[0] || await ctx.newPage()
await page.goto('https://elevenlabs.io/app/dubbing', { waitUntil: 'load' })
await page.waitForTimeout(3000)
await page.locator('text=Create a Dub').first().click()
await page.waitForTimeout(2500)

// what IS the language control?
const info = await page.evaluate(() => {
  const out = []
  for (const el of document.querySelectorAll('select')) {
    out.push({ tag: 'select', name: el.name || el.id || '(anon)', multiple: el.multiple,
      options: [...el.options].slice(0, 6).map(o => `${o.value}:${o.textContent.trim()}`) })
  }
  const cands = [...document.querySelectorAll('[role=combobox],[role=listbox],button')]
    .filter(e => /language/i.test(e.textContent || '') || /language/i.test(e.getAttribute('aria-label') || ''))
  for (const c of cands.slice(0, 6)) {
    out.push({ tag: c.tagName.toLowerCase(), role: c.getAttribute('role'),
      text: (c.textContent || '').trim().slice(0, 40),
      cls: (c.className || '').toString().slice(0, 60) })
  }
  return out
})
console.log(JSON.stringify(info, null, 1))
await ctx.close()
