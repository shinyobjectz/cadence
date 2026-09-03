#!/usr/bin/env node
// ellua-login: open a HEADED browser on a persistent profile so a human signs
// in once; recordings then reuse the authenticated session.
//
//   node login.mjs https://elevenlabs.io/app/sign-in [profileDir]
//
// Default profile: ~/.cache/ellua/chrome-profile. Sign in, then CLOSE THE
// BROWSER WINDOW — the session persists in the profile. Never commit the
// profile dir; it holds live cookies.

import { chromium } from 'playwright-core'
import { homedir } from 'os'

const url = process.argv[2] || 'https://elevenlabs.io/app/sign-in'
const profile = process.argv[3] || `${homedir()}/.cache/ellua/chrome-profile`

const context = await chromium.launchPersistentContext(profile, {
  channel: 'chrome',
  headless: false,
  viewport: { width: 1440, height: 900 },
})
const page = context.pages()[0] || (await context.newPage())
await page.goto(url)
console.log(`Sign in in the opened window, then close the browser.`)
console.log(`Session persists at: ${profile}`)
await new Promise((res) => context.on('close', res))
console.log('Profile saved. Recordings can now use --profile.')
