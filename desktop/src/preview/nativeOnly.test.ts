import { describe, expect, test } from 'vitest'
import { isNativeOnly, shouldPreview } from './nativeOnly'

describe('isNativeOnly', () => {
  test('hello.lua circle/rect/text is not native-only', () => {
    const source = `
      scene = function(s)
        s:circle { id = "dot", x = 180, y = 360, r = 60 }
        s:rect { id = "bar", x = 0, y = 600, w = 220, h = 56 }
        s:text { id = "title", text = "ellua" }
      end
    `
    expect(isNativeOnly(source)).toBe(false)
  })

  test('detects s:html, s:page, s:vector, s:fx, s:world, s:displace, s:draw', () => {
    expect(isNativeOnly('s:html { src = "x.html" }')).toBe(true)
    expect(isNativeOnly('s:page { src = "index.html" }')).toBe(true)
    expect(isNativeOnly('s:vector { src = "logo.svg" }')).toBe(true)
    expect(isNativeOnly('s:fx { chain = { "bloom" } }')).toBe(true)
    expect(isNativeOnly('s:world { fov = 0.7 }')).toBe(true)
    expect(isNativeOnly('s:displace { src = "map.png" }')).toBe(true)
    expect(isNativeOnly('s:draw { }')).toBe(true)
  })

  test('detects native-only node kinds in tables', () => {
    expect(isNativeOnly('kind = "html"')).toBe(true)
    expect(isNativeOnly("kind = 'vector'")).toBe(true)
    expect(isNativeOnly('kind = "world"')).toBe(true)
  })
})

describe('shouldPreview', () => {
  // CompNode mounts WasmoonFrame only when this is true (badge otherwise).
  test('allows wasmoon paint for canvas-2d comps like hello.lua', () => {
    expect(
      shouldPreview(`
        s:circle { id = "dot", r = 60 }
        s:rect { id = "bar", w = 220, h = 56 }
        s:text { id = "title", text = "ellua" }
      `),
    ).toBe(true)
  })

  test('skips wasmoon paint for native-only comps', () => {
    expect(shouldPreview('s:html { src = "x.html" }')).toBe(false)
    expect(shouldPreview('s:page { src = "index.html" }')).toBe(false)
    expect(shouldPreview('s:world { fov = 0.7 }')).toBe(false)
    expect(shouldPreview('kind = "fx"')).toBe(false)
  })
})
