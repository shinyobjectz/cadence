import { describe, expect, it } from 'vitest'
import { launchSpotExample } from '../../editor/exampleDoc'
import { parseDoc, serializeDoc } from './doc'

describe('parseDoc', () => {
  it('roundtrips launch spot example', () => {
    const doc = launchSpotExample()
    const raw = serializeDoc(doc)
    const parsed = parseDoc(raw)
    expect(parsed).toEqual(doc)
  })

  it('rejects missing version', () => {
    expect(() => parseDoc('{"duration":10,"fps":30,"aspect":"16:9","compPath":"x"}')).toThrow(
      /version/,
    )
  })

  it('rejects invalid aspect', () => {
    expect(() =>
      parseDoc(
        '{"version":1,"duration":10,"fps":30,"aspect":"4:3","compPath":"comps/a.lua","lines":[],"lineKeyframes":[],"scenes":[],"sceneParams":{}}',
      ),
    ).toThrow(/aspect/)
  })
})
