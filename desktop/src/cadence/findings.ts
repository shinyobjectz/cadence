/** cadence.result/v1 — shared between CLI output and desktop UI. */

export type FindingSeverity = 'error' | 'warn' | 'info'

export type CadenceFinding = {
  code: string
  severity: FindingSeverity
  node?: string
  detail?: string
  suggestion?: string
  t0?: number
  t1?: number
  measured?: number
  threshold?: number
  tier?: string
}

export type CadenceStep = {
  tier: string
  status: 'ok' | 'failed' | 'warn'
  duration_ms?: number
  findings?: CadenceFinding[]
  error?: string
  meta?: Record<string, number>
}

export type CadenceResult = {
  schema?: string
  command?: string
  status: 'ok' | 'failed' | 'degraded'
  exit_code: number
  comp?: string
  cwd?: string
  _meta?: { tier?: string; errors?: number; warnings?: number; findings?: number }
  meta?: Record<string, string | number | boolean>
  steps?: CadenceStep[]
  findings: CadenceFinding[]
  error?: string | null
}

/** Parse stdout from lint/check/verify (last JSON line). */
export function parseCadenceJson(stdout: string): CadenceResult | null {
  const line = stdout
    .trim()
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .pop()
  if (!line?.startsWith('{')) return null
  try {
    const raw = JSON.parse(line) as CadenceResult
    raw.findings = raw.findings ?? []
    return raw
  } catch {
    return null
  }
}

export function countFindings(result: CadenceResult | null) {
  const findings = result?.findings ?? []
  const errors = findings.filter((f) => f.severity === 'error').length
  const warnings = findings.filter((f) => f.severity === 'warn').length
  return { errors, warnings, total: findings.length }
}

/** One-line status for preview chrome. */
export function formatFindingsStatus(result: CadenceResult | null): string {
  if (!result) return ''
  const { errors, warnings } = countFindings(result)
  if (result.error && !result.findings.length) return result.error
  if (errors === 0 && warnings === 0) return ''
  const parts: string[] = []
  if (errors) parts.push(`${errors} error${errors === 1 ? '' : 's'}`)
  if (warnings) parts.push(`${warnings} warning${warnings === 1 ? '' : 's'}`)
  const top = result.findings.find((f) => f.severity === 'error') ?? result.findings[0]
  const hint = top ? `${top.code}: ${top.detail ?? ''}` : ''
  return `${parts.join(', ')} — ${hint}`.trim()
}

/** Markdown-ish block for Check node / agent handoff. */
export function formatFindingsBlock(result: CadenceResult | null): string {
  if (!result) return ''
  const { errors, warnings, total } = countFindings(result)
  if (total === 0 && result.status === 'ok') return 'ok — no findings'
  const lines = [`${result.command ?? 'check'}: ${result.status} (${errors} errors, ${warnings} warnings)`]
  for (const f of result.findings.slice(0, 12)) {
    const loc = [f.node, f.t0 != null ? `@${f.t0.toFixed(2)}s` : ''].filter(Boolean).join(' ')
    lines.push(`${f.severity} ${f.code}${loc ? ` ${loc}` : ''}: ${f.detail ?? ''}`)
  }
  if (result.findings.length > 12) lines.push(`… +${result.findings.length - 12} more`)
  return lines.join('\n')
}
