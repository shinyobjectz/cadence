#!/usr/bin/env node
/**
 * cadence verify — tiered verification pipeline for agents.
 * Usage: node scripts/cadence-verify.mjs <comp.lua> [--json] [--strict] [--skip-lint] [--skip-check] [--wasm-only]
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { wasmCompile, ROOT } from './lib/cadence-wasm.mjs'
import { loadBoundCompSource } from './lib/doc-binding.mjs'

const args = process.argv.slice(2)
const json = args.includes('--json')
const strict = args.includes('--strict')
const skipLint = args.includes('--skip-lint')
const skipCheck = args.includes('--skip-check')
const wasmOnly = args.includes('--wasm-only')
const compArg = args.find((a) => a.endsWith('.lua') && !a.startsWith('--'))
if (!compArg) {
  console.error('usage: cadence verify <comp.lua> [--json] [--strict] [--skip-lint] [--skip-check] [--wasm-only]')
  process.exit(2)
}

const cwd = process.cwd()
const comp = compArg.replace(/\\/g, '/')
const compAbs = path.isAbsolute(comp) ? comp : path.join(cwd, comp)
const started = Date.now()

/** @type {import('./cadence-types.d.ts').CadenceStep[]} */
const steps = []
/** @type {import('./cadence-types.d.ts').CadenceFinding[]} */
const findings = []

function pushFinding(f) {
  findings.push(f)
}

function parseLoveJson(stdout) {
  const line = stdout.trim().split('\n').filter(Boolean).pop()
  if (!line) return null
  try {
    return JSON.parse(line)
  } catch {
    return null
  }
}

function runCadence(mode, extraArgs = []) {
  const bin = path.join(ROOT, 'bin/cadence')
  const r = spawnSync(bin, [mode, comp, '--json', ...extraArgs], {
    cwd,
    encoding: 'utf8',
    env: { ...process.env, CADENCE_CWD: cwd, ELLUA_CWD: cwd },
  })
  return r
}

async function main() {
  let source
  try {
    source = await fs.readFile(compAbs, 'utf8')
    source = await loadBoundCompSource(cwd, comp, source)
  } catch (err) {
    const detail = `cannot read comp: ${err.message}`
    pushFinding({ code: 'io_error', severity: 'error', detail })
    emit('failed', 2, detail)
    return
  }

  // Tier 0 — wasmoon compile (instant, no LÖVE)
  const t0 = Date.now()
  try {
    const wasm = await wasmCompile(source)
    if (!wasm.ok) {
      pushFinding({ code: 'compile_error', severity: 'error', detail: wasm.error, tier: 'compile' })
      steps.push({
        tier: 'compile',
        status: 'failed',
        duration_ms: Date.now() - t0,
        findings: [{ code: 'compile_error', severity: 'error', detail: wasm.error }],
        error: wasm.error,
      })
      emit('failed', 1)
      return
    }
    steps.push({
      tier: 'compile',
      status: 'ok',
      duration_ms: Date.now() - t0,
      findings: [],
      meta: { width: wasm.width, height: wasm.height, duration: wasm.duration, fps: wasm.fps },
    })
  } catch (err) {
    const detail = String(err?.message || err)
    pushFinding({ code: 'wasm_error', severity: 'error', detail })
    steps.push({ tier: 'compile', status: 'failed', duration_ms: Date.now() - t0, findings: [], error: detail })
    emit('failed', 2, detail)
    return
  }

  if (wasmOnly) {
    emit('ok', 0)
    return
  }

  if (!skipLint) {
    const t1 = Date.now()
    const lint = runCadence('lint', strict ? ['--strict'] : [])
    const doc = parseLoveJson(lint.stdout || '')
    const tierFindings = doc?.findings || []
    for (const f of tierFindings) pushFinding({ ...f, tier: 'lint' })
    const errs = tierFindings.filter((f) => f.severity === 'error').length
    steps.push({
      tier: 'lint',
      status: lint.status !== 0 || errs > 0 ? 'failed' : 'ok',
      duration_ms: Date.now() - t1,
      findings: tierFindings,
      error: lint.status !== 0 && !doc ? (lint.stderr || lint.stdout || 'lint failed').trim() : undefined,
    })
    if (lint.status !== 0 && !doc) {
      emit('failed', lint.status || 1, steps.at(-1)?.error)
      return
    }
  }

  if (!skipCheck) {
    const t2 = Date.now()
    const check = runCadence('check', strict ? ['--strict'] : [])
    const doc = parseLoveJson(check.stdout || '')
    const tierFindings = doc?.findings || []
    for (const f of tierFindings) pushFinding({ ...f, tier: 'check' })
    const errs = tierFindings.filter((f) => f.severity === 'error').length
    steps.push({
      tier: 'check',
      status: check.status !== 0 || errs > 0 ? 'failed' : 'ok',
      duration_ms: Date.now() - t2,
      findings: tierFindings,
      error: check.status !== 0 && !doc ? (check.stderr || check.stdout || 'check failed').trim() : undefined,
    })
    if (check.status !== 0 && !doc) {
      emit('failed', check.status || 1, steps.at(-1)?.error)
      return
    }
  }

  const errs = findings.filter((f) => f.severity === 'error').length
  const warns = findings.filter((f) => f.severity === 'warn').length
  const failed = errs > 0 || (strict && warns > 0)
  emit(failed ? 'failed' : 'ok', failed ? 1 : 0)
}

function emit(status, exitCode, error) {
  const errs = findings.filter((f) => f.severity === 'error').length
  const warns = findings.filter((f) => f.severity === 'warn').length
  const envelope = {
    schema: 'cadence.result/v1',
    command: 'verify',
    status,
    exit_code: exitCode,
    comp,
    cwd,
    _meta: { tier: 'verify', errors: errs, warnings: warns, findings: findings.length },
    meta: {
      tier: 'verify',
      duration_ms: Date.now() - started,
      strict,
      wasm_only: wasmOnly,
      engine: 'cadence-verify',
    },
    steps,
    findings,
    error: error || null,
  }

  if (json) {
    console.log(JSON.stringify(envelope))
  } else {
    console.log(`cadence verify — ${status} (${errs} errors, ${warns} warnings)`)
    for (const step of steps) {
      const n = step.findings?.length || 0
      console.log(`  [${step.status}] ${step.tier} (${step.duration_ms}ms, ${n} findings)`)
      if (step.error) console.log(`         ${step.error}`)
    }
    for (const f of findings) {
      console.log(
        `  ${f.severity.toUpperCase()} ${f.code}${f.node ? ` [${f.node}]` : ''}: ${f.detail || ''}`,
      )
    }
  }
  process.exit(exitCode)
}

main().catch((err) => {
  console.error(err)
  process.exit(2)
})
