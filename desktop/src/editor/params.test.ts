import { describe, expect, it } from 'vitest'
import { filterParamInput, validateParam } from '../editor/params'

describe('validateParam', () => {
  it('accepts integers', () => {
    expect(validateParam('integer', '42')).toEqual({ ok: true, value: '42' })
    expect(validateParam('integer', '-3')).toEqual({ ok: true, value: '-3' })
  })

  it('rejects non-integers', () => {
    expect(validateParam('integer', '3.14').ok).toBe(false)
    expect(validateParam('integer', 'abc').ok).toBe(false)
  })

  it('accepts floats', () => {
    expect(validateParam('float', '0.4').ok).toBe(true)
    expect(validateParam('float', '-2.5').ok).toBe(true)
  })

  it('validates JSON list', () => {
    expect(validateParam('list', '["a","b"]').ok).toBe(true)
    expect(validateParam('list', '{"a":1}').ok).toBe(false)
  })

  it('validates JSON map', () => {
    expect(validateParam('map', '{"a":1}').ok).toBe(true)
    expect(validateParam('map', '[]').ok).toBe(false)
  })
})

describe('filterParamInput', () => {
  it('strips letters from integer input', () => {
    expect(filterParamInput('integer', '12a3', '')).toBe('123')
  })

  it('allows one decimal in float', () => {
    expect(filterParamInput('float', '1.2.3', '')).toBe('1.23')
  })
})
