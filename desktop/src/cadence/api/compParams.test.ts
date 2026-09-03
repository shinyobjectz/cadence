import { describe, expect, it } from 'vitest'
import { injectDocBinding } from './docBinding'
import { launchSpotExample } from '../../editor/exampleDoc'

describe('compParams', () => {
  it('injects logoScale from scene params via doc binding', () => {
    const doc = launchSpotExample()
    const out = injectDocBinding('return {}\n', doc)
    expect(out).toContain('__doc')
    expect(out).toContain('logoScale')
    expect(out).toContain('1.08')
  })

  it('builds brandColor map in __doc.params', () => {
    const doc = launchSpotExample()
    const out = injectDocBinding('return {}\n', doc)
    expect(out).toContain('brandColor')
    expect(out).toContain('#7c3aed')
  })
})
