import { describe, expect, it } from 'vitest'
import { launchSpotExample } from './exampleDoc'
import { transcriptPlayheadAt, transcriptEndTime } from './transcriptPlayhead'

describe('transcriptPlayheadAt', () => {
  const doc = launchSpotExample()

  it('maps word hits during VO', () => {
    const hit = transcriptPlayheadAt(doc, 2.9)
    expect(hit?.kind).toBe('word')
    if (hit?.kind === 'word') expect(hit.word.text).toBe('scripts')
  })

  it('maps inter-line silence to leading gap of next line', () => {
    const hit = transcriptPlayheadAt(doc, 1.5)
    expect(hit).toEqual({ kind: 'gap', lineId: 'vo-pitch', gapIndex: 0 })
  })

  it('maps editorial tail after VO end', () => {
    const voEnd = transcriptEndTime(doc)
    expect(voEnd).toBeCloseTo(6.78)
    const hit = transcriptPlayheadAt(doc, 8)
    expect(hit?.kind).toBe('editorial')
    if (hit?.kind === 'editorial') expect(hit.sceneId).toBe('sc-montage')
  })

  it('maps editorial gap before montage scene', () => {
    const hit = transcriptPlayheadAt(doc, 7)
    expect(hit?.kind).toBe('editorial')
    if (hit?.kind === 'editorial') expect(hit.sceneId).toBeNull()
  })
})
