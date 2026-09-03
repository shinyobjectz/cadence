import { describe, expect, it } from 'vitest'
import { timeToPct, visibleRange } from '../editor/timeline'

describe('visibleRange', () => {
  it('shows full duration at zoom 1', () => {
    const r = visibleRange(10, 5, 1)
    expect(r.viewStart).toBe(0)
    expect(r.viewEnd).toBe(10)
  })

  it('zooms in around playhead', () => {
    const r = visibleRange(10, 5, 2)
    expect(r.viewStart).toBe(2.5)
    expect(r.viewEnd).toBe(7.5)
  })
})

describe('timeToPct', () => {
  it('maps time within view', () => {
    expect(timeToPct(5, 0, 10)).toBe(50)
  })
})
