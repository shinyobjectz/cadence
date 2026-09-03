import { chromium } from 'playwright-core'
const b = await chromium.launch({ channel: 'chrome', headless: false })
const p = await b.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 3 })
await p.goto('https://elevenlabs.io/', { waitUntil: 'load', timeout: 60000 })
await p.waitForTimeout(5000)
// render the wordmark large on a dark page, then shoot it
await p.evaluate(() => {
  const svg = document.querySelector('header svg, a[href="/"] svg')
  document.body.innerHTML = ''
  document.body.style.cssText = 'background:#080808;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'
  const box = document.createElement('div')
  box.id = 'shot'
  box.style.cssText = 'padding:40px 60px;display:flex;align-items:center;justify-content:center'
  if (svg) {
    const c = svg.cloneNode(true)
    c.style.cssText = 'width:760px;height:auto;color:#fff;fill:#fff'
    c.querySelectorAll('*').forEach((n) => { n.setAttribute('fill', '#ffffff') })
    box.appendChild(c)
  } else {
    box.innerHTML = '<div style="color:#fff;font:700 96px Helvetica,Arial">IIElevenLabs</div>'
  }
  document.body.appendChild(box)
})
await p.waitForTimeout(1200)
await p.locator('#shot').screenshot({ path: '/tmp/logo_raw.png' })
await b.close()
console.log('captured')
