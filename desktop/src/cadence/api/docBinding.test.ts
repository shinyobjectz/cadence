import { describe, expect, it } from 'vitest'
import { launchSpotExample } from '../../editor/exampleDoc'
import { parseDoc, serializeDoc } from './doc'
import { buildDocBinding, buildDocLua, injectDocBinding } from './docBinding'

describe('buildDocBinding', () => {
  it('resolves keyframe times from transcript words', () => {
    const binding = buildDocBinding(launchSpotExample())
    const logo = binding.keyframes.find((k) => k.id === 'kf-logo-hit')
    expect(logo?.t).toBeCloseTo(0.78)
    const ship = binding.keyframes.find((k) => k.id === 'kf-ship')
    expect(ship?.t).toBeCloseTo(6.32)
    const video = binding.keyframes.find((k) => k.id === 'kf-video')
    expect(video?.t).toBeCloseTo(4.36)
  })

  it('roundtrips doc.json shape through parse/serialize', () => {
    const doc = launchSpotExample()
    const parsed = parseDoc(serializeDoc(doc))
    expect(buildDocBinding(parsed)).toEqual(buildDocBinding(doc))
  })

  it('exposes voEnd for comp editorial tail', () => {
    const binding = buildDocBinding(launchSpotExample())
    expect(binding.voEnd).toBeCloseTo(6.78)
  })

  it('injects __doc prelude for comp compile', () => {
    const out = injectDocBinding('return {}\n', launchSpotExample())
    expect(out).toContain('__doc =')
    expect(out).toContain('kf-logo-hit')
    expect(out).toContain('scripts')
    expect(out).toContain('montageCuts')
  })

  it('buildDocLua includes scene blocks', () => {
    const lua = buildDocLua(launchSpotExample())
    expect(lua).toContain('sc-open')
    expect(lua).toContain('sc-cta')
    expect(lua).toContain('vo-hook')
  })

  it('binds transcript lines for caption grouping', () => {
    const binding = buildDocBinding(launchSpotExample())
    expect(binding.lines).toHaveLength(3)
    expect(binding.lines[0]?.words).toHaveLength(2)
  })
})
