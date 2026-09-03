import { describe, expect, it } from 'vitest'
import { keyframeTime } from '../editor/time'

describe('keyframeTime', () => {
  const words = [
    { start: 0, end: 0.4 },
    { start: 0.4, end: 0.6 },
    { start: 0.6, end: 1.0 },
  ]

  it('open anchors to next word start', () => {
    expect(keyframeTime('open', words, 1)).toBe(0.4)
  })

  it('close anchors to previous word end', () => {
    expect(keyframeTime('close', words, 2)).toBe(0.6)
  })

  it('mid averages between words', () => {
    const spaced = [
      { start: 0, end: 0.4 },
      { start: 0.6, end: 1.0 },
    ]
    expect(keyframeTime('mid', spaced, 1)).toBeCloseTo(0.5)
  })
})
