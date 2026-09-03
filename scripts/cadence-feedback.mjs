#!/usr/bin/env node
/**
 * cadence feedback — agent-readable summary from verify JSON or inline run.
 * Usage: node scripts/cadence-feedback.mjs [comp.lua] [--json] [--from FILE]
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const args = process.argv.slice(2)
const jsonOut = args.includes('--json')
const fromIdx = args.indexOf('--from')
const comp = args.find((a) => a.endsWith('.lua') && !a.startsWith('--'))

async function loadEnvelope() {
  if (fromIdx >= 0) {
    const raw = await fs.readFile(args[fromIdx + 1], 'utf8')
    return JSON.parse(raw)
  }
  if (!comp) {
    throw new Error('usage: cadence feedback <comp.lua> [--json] | --from result.json')
  }
  const script = path.join(ROOT, 'scripts/cadence-verify.mjs')
  const r = spawnSync(process.execPath, [script, comp, '--json'], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, CADENCE_CWD: process.cwd(), ELLUA_CWD: process.cwd() },
  })
  const line = (r.stdout || '').trim().split('\n').filter(Boolean).pop()
  if (!line) throw new Error(r.stderr || r.stdout || 'verify produced no JSON')
  return JSON.parse(line)
}

function summarize(doc) {
  const lines = []
  lines.push(`# cadence feedback — ${doc.comp || 'unknown'}`)
  lines.push(`status: ${doc.status} (exit ${doc.exit_code})`)
  if (doc.meta?.duration_ms) lines.push(`duration: ${doc.meta.duration_ms}ms`)
  lines.push('')

  if (doc.steps?.length) {
    lines.push('## pipeline')
    for (const s of doc.steps) {
      lines.push(`- ${s.tier}: ${s.status} (${s.duration_ms ?? '?'}ms)`)
    }
    lines.push('')
  }

  const findings = doc.findings || []
  if (!findings.length) {
    lines.push('No findings — comp passed all tiers run.')
    return lines.join('\n')
  }

  lines.push(`## findings (${findings.length})`)
  for (const f of findings) {
    const loc = [f.node, f.t0 != null ? `@${f.t0.toFixed(2)}s` : ''].filter(Boolean).join(' ')
    lines.push(`- **${f.severity}** \`${f.code}\`${loc ? ` ${loc}` : ''}: ${f.detail || ''}`)
    if (f.suggestion) lines.push(`  - fix: ${f.suggestion}`)
  }

  lines.push('')
  lines.push('## agent next steps')
  if (findings.some((f) => f.code === 'compile_error')) {
    lines.push('- Fix compile/syntax errors before lint or check.')
  }
  if (findings.some((f) => f.tier === 'lint' || !f.tier)) {
    lines.push('- Run `cadence lint comp.lua --json` for full lint detail.')
  }
  if (findings.some((f) => f.code === 'contrast_measured' || f.code === 'blank_frame')) {
    lines.push('- Pixel issues need visual check: `cadence check --snapshots` when available.')
  }
  lines.push('- Re-run: `cadence verify comp.lua --json`')

  return lines.join('\n')
}

const doc = await loadEnvelope()
const text = summarize(doc)
if (jsonOut) {
  console.log(JSON.stringify({ schema: 'cadence.feedback/v1', comp: doc.comp, status: doc.status, markdown: text, envelope: doc }))
} else {
  console.log(text)
}
process.exit(doc.exit_code ?? (doc.status === 'ok' ? 0 : 1))
