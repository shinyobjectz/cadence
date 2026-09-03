#!/usr/bin/env node
/**
 * cadence doctor — environment probes for agents and CI.
 * Usage: node scripts/cadence-doctor.mjs [--json] [--project DIR]
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const args = process.argv.slice(2)
const json = args.includes('--json')
const projectIdx = args.indexOf('--project')
const project = projectIdx >= 0 ? path.resolve(args[projectIdx + 1] || '.') : process.cwd()

function probe(name, ok, detail, severity = ok ? 'ok' : 'error') {
  return { name, ok, severity, detail }
}

function which(bin) {
  const r = spawnSync('which', [bin], { encoding: 'utf8' })
  return r.status === 0 ? r.stdout.trim() : null
}

function loveBin() {
  if (process.env.LOVE_BIN && fs.existsSync(process.env.LOVE_BIN)) return process.env.LOVE_BIN
  const candidates = [
    path.join(ROOT, 'build/ellua-love-macos-mega/love/ellua-love'),
    path.join(ROOT, 'vendor/love12.app/Contents/MacOS/love'),
    which('love'),
    '/Applications/love.app/Contents/MacOS/love',
  ].filter(Boolean)
  for (const c of candidates) {
    if (c && fs.existsSync(c)) return c
  }
  return null
}

function nodeModuleOk(rel) {
  const p = path.join(ROOT, rel)
  return fs.existsSync(p) ? p : null
}

const checks = []

checks.push(probe('cadence_root', fs.existsSync(path.join(ROOT, 'runtime/main.lua')), ROOT))
checks.push(probe('cadence_lib', fs.existsSync(path.join(ROOT, 'lib/cadence/init.lua')), 'lib/cadence/init.lua'))
checks.push(probe('bridge_lua', fs.existsSync(path.join(ROOT, 'web/bridge.lua')), 'web/bridge.lua'))

const love = loveBin()
checks.push(probe('ellua_love', Boolean(love), love || 'set LOVE_BIN or install LÖVE'))

const ffmpeg = which('ffmpeg')
checks.push(probe('ffmpeg', Boolean(ffmpeg), ffmpeg || 'install ffmpeg'))

const ffprobe = which('ffprobe')
checks.push(probe('ffprobe', Boolean(ffprobe), ffprobe || 'install ffprobe'))

const node = process.version
checks.push(probe('node', true, node))

const wasmoon = nodeModuleOk('desktop/node_modules/wasmoon/dist/glue.wasm')
checks.push(
  probe(
    'wasmoon',
    Boolean(wasmoon),
    wasmoon ? 'desktop/node_modules/wasmoon' : 'run pnpm install in desktop/',
    wasmoon ? 'ok' : 'warn',
  ),
)

const docJson = path.join(project, 'doc.json')
checks.push(
  probe(
    'project_doc',
    fs.existsSync(docJson),
    fs.existsSync(docJson) ? docJson : 'no doc.json in project cwd',
    fs.existsSync(docJson) ? 'ok' : 'warn',
  ),
)

const compsDir = path.join(project, 'comps')
checks.push(
  probe(
    'project_comps',
    fs.existsSync(compsDir),
    fs.existsSync(compsDir) ? compsDir : 'no comps/ directory',
    fs.existsSync(compsDir) ? 'ok' : 'warn',
  ),
)

const required = ['cadence_root', 'cadence_lib', 'ellua_love', 'ffmpeg', 'ffprobe', 'node']
const failed = checks.filter((c) => !c.ok && required.includes(c.name))
const warnings = checks.filter((c) => c.severity === 'warn' || (!c.ok && !required.includes(c.name)))
const status = failed.length > 0 ? 'failed' : warnings.length > 0 ? 'degraded' : 'ok'
const exitCode = failed.length > 0 ? 2 : 0

const envelope = {
  schema: 'cadence.result/v1',
  command: 'doctor',
  status,
  exit_code: exitCode,
  cwd: project,
  meta: { tier: 'doctor', checks: checks.length, failed: failed.length, warnings: warnings.length },
  findings: checks
    .filter((c) => !c.ok)
    .map((c) => ({
      code: `doctor_${c.name}`,
      severity: required.includes(c.name) ? 'error' : 'warn',
      detail: c.detail,
      suggestion: c.name === 'wasmoon' ? 'cd desktop && pnpm install' : undefined,
    })),
  steps: checks.map((c) => ({
    tier: c.name,
    status: c.ok ? 'ok' : c.severity === 'warn' ? 'warn' : 'failed',
    detail: c.detail,
  })),
}

if (json) {
  console.log(JSON.stringify(envelope))
} else {
  console.log(`cadence doctor — ${status}`)
  for (const c of checks) {
    const mark = c.ok ? 'ok' : c.severity === 'warn' ? 'warn' : 'FAIL'
    console.log(`  [${mark}] ${c.name}: ${c.detail}`)
  }
  if (failed.length) {
    console.error(`\n${failed.length} required probe(s) failed`)
  }
}

process.exit(exitCode)
