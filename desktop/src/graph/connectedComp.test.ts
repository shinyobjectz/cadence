import { describe, expect, test } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { connectedCheckLua, connectedCompLua } from './connectedComp'

function node(id: string, type: string, data: Record<string, unknown> = {}): Node {
  return { id, type, position: { x: 0, y: 0 }, data }
}

function edge(
  source: string,
  target: string,
  targetHandle?: string | null,
): Edge {
  return {
    id: `${source}-${target}-${targetHandle ?? 'default'}`,
    source,
    target,
    targetHandle,
  }
}

describe('connectedCompLua', () => {
  test('returns the lua path on the comp handle and ignores audio', () => {
    const nodes = [
      node('comp', 'comp', { luaPath: 'comps/hello.lua' }),
      node('render', 'render'),
    ]
    const edges = [
      edge('comp', 'render', 'comp'),
      edge('tts', 'render', 'audio'),
    ]

    expect(connectedCompLua('render', nodes, edges)).toBe('comps/hello.lua')
  })

  test('returns null when no comp is wired', () => {
    const nodes = [node('render', 'render')]
    expect(connectedCompLua('render', nodes, [])).toBeNull()
  })
})

describe('connectedCheckLua', () => {
  test('reads lua from a directly connected comp', () => {
    const nodes = [
      node('comp', 'comp', { luaPath: 'comps/hello.lua' }),
      node('check', 'check'),
    ]
    const edges = [edge('comp', 'check', 'in')]

    expect(connectedCheckLua('check', nodes, edges)).toBe('comps/hello.lua')
  })

  test('walks through a connected render to its comp', () => {
    const nodes = [
      node('comp', 'comp', { luaPath: 'comps/via-render.lua' }),
      node('render', 'render'),
      node('check', 'check'),
    ]
    const edges = [
      edge('comp', 'render', 'comp'),
      edge('render', 'check', 'in'),
    ]

    expect(connectedCheckLua('check', nodes, edges)).toBe('comps/via-render.lua')
  })

  test('returns null when a render has no upstream comp', () => {
    const nodes = [node('render', 'render'), node('check', 'check')]
    const edges = [edge('render', 'check', 'in')]

    expect(connectedCheckLua('check', nodes, edges)).toBeNull()
  })
})
