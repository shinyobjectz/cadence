import { describe, expect, test } from 'vitest'
import { costChip, IMAGE_MODELS, TEXT_MODELS, VIDEO_MODELS, type Model } from './gateway'

describe('gateway models', () => {
  test('text/image/video lists use Tersa-style cost chips', () => {
    const assertModel = (model: Model) => {
      expect([1, 2, 3]).toContain(model.cost)
      expect(costChip(model.cost)).toBe('$'.repeat(model.cost))
    }
    TEXT_MODELS.forEach(assertModel)
    IMAGE_MODELS.forEach(assertModel)
    VIDEO_MODELS.forEach(assertModel)
    expect(TEXT_MODELS.map((m) => m.id)).toEqual(['gpt-4o-mini', 'gpt-4o'])
    expect(IMAGE_MODELS.map((m) => m.id)).toEqual(['gpt-image-1'])
    expect(VIDEO_MODELS.map((m) => m.id)).toEqual(['placeholder-video'])
    expect(costChip(1)).toBe('$')
    expect(costChip(2)).toBe('$$')
    expect(costChip(3)).toBe('$$$')
  })
})
