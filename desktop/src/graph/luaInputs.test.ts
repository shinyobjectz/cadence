import { describe, expect, test } from 'vitest'
import { scanLuaInputs } from './luaInputs'

const FIXTURE = `
inputs = {
  bg = { kind = "video" },
  logo = { kind = "image", default = "assets/logo.png" },
}
`

describe('scanLuaInputs', () => {
  test('parses name and kind from an inputs table fixture', () => {
    expect(scanLuaInputs(FIXTURE)).toEqual([
      { name: 'bg', kind: 'video' },
      { name: 'logo', kind: 'image' },
    ])
  })

  test('finds inputs nested inside e.comp { ... }', () => {
    const source = `
local e = require("ellua")
return e.comp {
  width = 64, height = 64, duration = 0.1, fps = 10,
  inputs = {
    bg = { kind = "image" },
  },
  scene = function(s) end,
}
`
    expect(scanLuaInputs(source)).toEqual([{ name: 'bg', kind: 'image' }])
  })

  test('returns an empty list when there is no inputs table', () => {
    expect(scanLuaInputs('return e.comp { width = 64, height = 64, duration = 1, scene = function() end }')).toEqual([])
  })
})
