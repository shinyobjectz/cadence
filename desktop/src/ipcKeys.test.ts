// ipcKeys.test.ts — Tauri 2 expects camelCase command args
import { describe, expect, test } from 'vitest'
import app from './App.tsx?raw'
import renderNode from './nodes/RenderNode.tsx?raw'
import checkNode from './nodes/CheckNode.tsx?raw'
import ttsNode from './nodes/TtsNode.tsx?raw'

const files: [string, string][] = [
  ['src/App.tsx', app],
  ['src/nodes/RenderNode.tsx', renderNode],
  ['src/nodes/CheckNode.tsx', checkNode],
  ['src/nodes/TtsNode.tsx', ttsNode],
]

describe('Tauri invoke arg keys', () => {
  test('command payloads use camelCase, not snake_case', () => {
    for (const [name, src] of files) {
      expect(src, name).not.toMatch(/lua_rel:/)
      expect(src, name).not.toMatch(/file_rel:/)
      expect(src, name).not.toMatch(/node_id:/)
    }
  })
})
