import { describe, expect, it } from 'vitest'
import {
  formatFindingsBlock,
  formatFindingsStatus,
  parseCadenceJson,
} from './findings'

describe('parseCadenceJson', () => {
  it('parses last JSON line from mixed stdout', () => {
    const stdout = `ellua error noise\n{"schema":"cadence.result/v1","status":"failed","exit_code":1,"findings":[{"code":"compile_error","severity":"error","detail":"syntax"}]}`
    const doc = parseCadenceJson(stdout)
    expect(doc?.findings).toHaveLength(1)
    expect(doc?.findings[0].code).toBe('compile_error')
  })

  it('formats status and block for agents', () => {
    const doc = parseCadenceJson(
      JSON.stringify({
        status: 'failed',
        exit_code: 1,
        command: 'lint',
        findings: [
          { code: 'motion_density', severity: 'warn', detail: 'too much motion' },
          { code: 'off_frame', severity: 'error', detail: 'text off canvas', node: 'title' },
        ],
      }),
    )
    expect(formatFindingsStatus(doc)).toContain('1 error')
    expect(formatFindingsBlock(doc)).toContain('off_frame')
  })
})
