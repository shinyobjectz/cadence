import { describe, expect, test } from 'vitest'
import { canConnect, connectionAllowed, resolveHandleKind } from './handles'
import { contentHashes, dirty, plan, stale } from './runner'
import type { GraphEdge, GraphNode } from './types'

const chain = (
  ids: string[],
): { nodes: GraphNode[]; edges: GraphEdge[] } => ({
  nodes: ids.map((id) => ({ id })),
  edges: ids.slice(0, -1).map((source, i) => ({
    source,
    target: ids[i + 1]!,
  })),
})

describe('canConnect', () => {
  test('rejects audio → comp', () => {
    expect(canConnect('audio', 'comp')).toBe(false)
  })

  test('rejects image → comp', () => {
    expect(canConnect('image', 'comp')).toBe(false)
  })

  test('rejects video, text, font, and brand → comp', () => {
    expect(canConnect('video', 'comp')).toBe(false)
    expect(canConnect('text', 'comp')).toBe(false)
    expect(canConnect('font', 'comp')).toBe(false)
    expect(canConnect('brand', 'comp')).toBe(false)
  })

  test('allows same-kind connections', () => {
    expect(canConnect('image', 'image')).toBe(true)
    expect(canConnect('video', 'video')).toBe(true)
    expect(canConnect('audio', 'audio')).toBe(true)
    expect(canConnect('text', 'text')).toBe(true)
    expect(canConnect('font', 'font')).toBe(true)
    expect(canConnect('brand', 'brand')).toBe(true)
    expect(canConnect('file', 'file')).toBe(true)
    expect(canConnect('comp', 'comp')).toBe(true)
  })

  test('allows audio to file and render, not preview/check/image', () => {
    expect(canConnect('audio', 'render')).toBe(true)
    expect(canConnect('audio', 'file')).toBe(true)
    expect(canConnect('audio', 'preview')).toBe(false)
    expect(canConnect('audio', 'check')).toBe(false)
    expect(canConnect('audio', 'image')).toBe(false)
  })

  test('allows video and comp into check, still rejects audio', () => {
    expect(canConnect('comp', 'check')).toBe(true)
    expect(canConnect('video', 'check')).toBe(true)
    expect(canConnect('audio', 'check')).toBe(false)
  })

  test('allows image, video, text, font, and brand to file', () => {
    expect(canConnect('image', 'file')).toBe(true)
    expect(canConnect('video', 'file')).toBe(true)
    expect(canConnect('text', 'file')).toBe(true)
    expect(canConnect('font', 'file')).toBe(true)
    expect(canConnect('brand', 'file')).toBe(true)
  })

  test('rejects mismatched media kinds', () => {
    expect(canConnect('image', 'video')).toBe(false)
    expect(canConnect('video', 'image')).toBe(false)
    expect(canConnect('text', 'image')).toBe(false)
    expect(canConnect('image', 'render')).toBe(false)
  })

  test('allows comp output to preview, render, check, and comp', () => {
    expect(canConnect('comp', 'preview')).toBe(true)
    expect(canConnect('comp', 'render')).toBe(true)
    expect(canConnect('comp', 'check')).toBe(true)
    expect(canConnect('comp', 'comp')).toBe(true)
    expect(canConnect('comp', 'image')).toBe(false)
  })

  test('resolves file output kind from data.kind even when handle id is out', () => {
    expect(resolveHandleKind('file', { kind: 'image' }, 'out', 'source')).toBe('image')
    expect(resolveHandleKind('file', { kind: 'audio' }, 'out', 'source')).toBe('audio')
    expect(
      resolveHandleKind(
        'comp',
        { inputs: [{ name: 'bg', kind: 'video' }] },
        'bg',
        'target',
      ),
    ).toBe('video')
    expect(resolveHandleKind('comp', { inputs: [] }, 'comp', 'source')).toBe('comp')
    expect(resolveHandleKind('render', {}, 'out', 'source')).toBe('video')
    expect(resolveHandleKind('render', {}, 'comp', 'target')).toBe('comp')
    expect(resolveHandleKind('render', {}, 'audio', 'target')).toBe('audio')
    expect(resolveHandleKind('text', {}, 'text', 'source')).toBe('text')
    expect(resolveHandleKind('text', {}, 'out', 'source')).toBe('text')
    expect(resolveHandleKind('image-gen', {}, 'image', 'source')).toBe('image')
    expect(resolveHandleKind('image-gen', {}, 'text', 'target')).toBe('text')
    expect(resolveHandleKind('video-gen', {}, 'video', 'source')).toBe('video')
    expect(resolveHandleKind('video-gen', {}, 'text', 'target')).toBe('text')
    expect(
      resolveHandleKind(
        'capture',
        { outputs: [{ id: 'page', kind: 'file' }, { id: 'brand', kind: 'brand' }] },
        'page',
        'source',
      ),
    ).toBe('file')
    expect(
      resolveHandleKind(
        'capture',
        { outputs: [{ id: 'page', kind: 'file' }, { id: 'brand', kind: 'brand' }] },
        'brand',
        'source',
      ),
    ).toBe('brand')
    expect(
      resolveHandleKind(
        'capture',
        { outputs: [{ id: 'font-Inter-ttf', kind: 'font' }] },
        'font-Inter-ttf',
        'source',
      ),
    ).toBe('font')
    expect(resolveHandleKind('tts', {}, 'audio', 'source')).toBe('audio')
    expect(resolveHandleKind('tts', {}, 'text', 'target')).toBe('text')
    expect(resolveHandleKind('check', {}, 'in', 'target')).toBe('check')
    expect(resolveHandleKind('check', {}, 'comp', 'target')).toBe('comp')
    expect(resolveHandleKind('check', {}, 'video', 'target')).toBe('video')
    expect(canConnect('image', 'image')).toBe(true)
    expect(canConnect('text', 'text')).toBe(true)
    expect(canConnect('video', 'video')).toBe(true)
    expect(canConnect('image', 'comp')).toBe(false)
  })

  test('rejects audio into a Comp node even when the pin kind is audio', () => {
    expect(connectionAllowed('audio', 'audio', 'comp')).toBe(false)
    expect(connectionAllowed('audio', 'audio', 'render')).toBe(true)
    expect(connectionAllowed('image', 'image', 'comp')).toBe(true)
  })

  test('Check only accepts Comp and Render sources, not file or video-gen', () => {
    expect(connectionAllowed('comp', 'check', 'check', 'comp')).toBe(true)
    expect(connectionAllowed('video', 'check', 'check', 'render')).toBe(true)
    expect(connectionAllowed('video', 'check', 'check', 'video-gen')).toBe(false)
    expect(connectionAllowed('video', 'check', 'check', 'file')).toBe(false)
    expect(connectionAllowed('video', 'check', 'check', undefined)).toBe(false)
  })
})

describe('plan', () => {
  test('returns a valid topo order for a diamond', () => {
    const nodes: GraphNode[] = [
      { id: 'a' },
      { id: 'b' },
      { id: 'c' },
      { id: 'd' },
    ]
    const edges: GraphEdge[] = [
      { source: 'a', target: 'b' },
      { source: 'a', target: 'c' },
      { source: 'b', target: 'd' },
      { source: 'c', target: 'd' },
    ]

    const order = plan(nodes, edges)

    expect(order).toHaveLength(4)
    expect(new Set(order)).toEqual(new Set(['a', 'b', 'c', 'd']))
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('b'))
    expect(order.indexOf('a')).toBeLessThan(order.indexOf('c'))
    expect(order.indexOf('b')).toBeLessThan(order.indexOf('d'))
    expect(order.indexOf('c')).toBeLessThan(order.indexOf('d'))
  })

  test('errors on a 2-cycle', () => {
    const nodes: GraphNode[] = [{ id: 'a' }, { id: 'b' }]
    const edges: GraphEdge[] = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'a' },
    ]

    expect(() => plan(nodes, edges)).toThrow(/cycle/i)
  })

  test('ignores edges whose target is missing from the node set', () => {
    const nodes: GraphNode[] = [{ id: 'a' }]
    const edges: GraphEdge[] = [{ source: 'a', target: 'missing' }]

    expect(plan(nodes, edges)).toEqual(['a'])
  })

  test('ignores edges whose source is missing from the node set', () => {
    const nodes: GraphNode[] = [{ id: 'a' }]
    const edges: GraphEdge[] = [{ source: 'missing', target: 'a' }]

    expect(plan(nodes, edges)).toEqual(['a'])
  })
})

describe('dirty', () => {
  test('changing an upstream hash dirties downstream', () => {
    const { nodes, edges } = chain(['a', 'b', 'c'])
    const lastRunHashes = { a: 'ha', b: 'hb', c: 'hc' }
    const hashes = { a: 'ha-changed', b: 'hb', c: 'hc' }

    const ids = dirty(nodes, edges, hashes, lastRunHashes)

    expect(ids).toEqual(expect.arrayContaining(['b', 'c']))
    expect(ids).not.toContain('a')
  })

  test('unchanged hashes dirty nothing', () => {
    const { nodes, edges } = chain(['a', 'b', 'c'])
    const hashes = { a: 'ha', b: 'hb', c: 'hc' }

    expect(dirty(nodes, edges, hashes, hashes)).toEqual([])
  })

  test('returns dirty ids in topo order regardless of node list order', () => {
    const nodes: GraphNode[] = [{ id: 'c' }, { id: 'b' }, { id: 'a' }]
    const edges: GraphEdge[] = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
    ]
    const lastRunHashes = { a: 'ha', b: 'hb', c: 'hc' }
    const hashes = { a: 'ha-changed', b: 'hb', c: 'hc' }

    expect(dirty(nodes, edges, hashes, lastRunHashes)).toEqual(['b', 'c'])
  })

  test('throws cycle on a 2-cycle instead of overflowing', () => {
    const nodes: GraphNode[] = [{ id: 'a' }, { id: 'b' }]
    const edges: GraphEdge[] = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'a' },
    ]
    const hashes = { a: 'ha', b: 'hb' }

    expect(() => dirty(nodes, edges, hashes, hashes)).toThrow(/cycle/i)
  })
})

const fileCompRender = (): { nodes: GraphNode[]; edges: GraphEdge[] } => ({
  nodes: [
    { id: 'a', type: 'file', data: { path: 'assets/in/a.png', kind: 'image' } },
    { id: 'b', type: 'comp', data: { luaPath: 'comps/hello.lua' } },
    { id: 'c', type: 'render', data: { outputRel: 'renders/hello.mp4' } },
  ],
  edges: [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
  ],
})

describe('contentHashes', () => {
  test('file path and kind changes produce a new hash', () => {
    const node: GraphNode = {
      id: 'a',
      type: 'file',
      data: { path: 'assets/in/a.png', kind: 'image' },
    }
    const before = contentHashes([node])
    const after = contentHashes([
      { ...node, data: { path: 'assets/in/b.png', kind: 'image' } },
    ])
    expect(before.a).toBeTruthy()
    expect(after.a).not.toEqual(before.a)
  })

  test('hashes gen outputRel and prompt, capture outputs, and lua/render paths', () => {
    const hashes = contentHashes([
      { id: 'img', type: 'image-gen', data: { outputRel: 'assets/in/x.png', prompt: 'cat' } },
      { id: 'vid', type: 'video-gen', data: { outputRel: 'assets/in/x.mp4', prompt: 'pan' } },
      { id: 'tts', type: 'tts', data: { outputRel: 'assets/in/x.mp3', text: 'hello' } },
      {
        id: 'cap',
        type: 'capture',
        data: { outputs: [{ rel: 'assets/capture/ex/page.png' }, { rel: 'assets/capture/ex/brand.json' }] },
      },
      { id: 'comp', type: 'comp', data: { luaPath: 'comps/hello.lua' } },
      { id: 'rend', type: 'render', data: { outputRel: 'renders/hello.mp4' } },
      { id: 'chk', type: 'check', data: { findings: 'should-ignore' } },
    ])
    expect(hashes.img).toBeTruthy()
    expect(hashes.vid).toBeTruthy()
    expect(hashes.tts).toBeTruthy()
    expect(hashes.cap).toContain('page.png')
    expect(hashes.comp).toContain('hello.lua')
    expect(hashes.rend).toContain('hello.mp4')
    expect(hashes.chk).toBeUndefined()
  })

  test('render quality change produces a new hash', () => {
    const node: GraphNode = {
      id: 'r',
      type: 'render',
      data: { outputRel: 'renders/hello.mp4', quality: 'draft' },
    }
    const before = contentHashes([node])
    const after = contentHashes([
      { ...node, data: { outputRel: 'renders/hello.mp4', quality: 'high' } },
    ])
    expect(before.r).toBeTruthy()
    expect(after.r).not.toEqual(before.r)
  })
})

describe('stale', () => {
  test('file hash change marks downstream comp and render stale, not the file', () => {
    const { nodes, edges } = fileCompRender()
    const lastRunHashes = contentHashes(nodes)
    const hashes = contentHashes([
      { ...nodes[0]!, data: { path: 'assets/in/changed.png', kind: 'image' } },
      nodes[1]!,
      nodes[2]!,
    ])

    const ids = stale(nodes, edges, hashes, lastRunHashes)

    expect(ids).toEqual(['b', 'c'])
    expect(ids).not.toContain('a')
  })

  test('unchanged hashes yield no stale ids', () => {
    const { nodes, edges } = fileCompRender()
    const hashes = contentHashes(nodes)

    expect(stale(nodes, edges, hashes, hashes)).toEqual([])
  })

  test('returns stale comp/render ids in topo order and ignores dirty check nodes', () => {
    const nodes: GraphNode[] = [
      { id: 'c', type: 'render', data: { outputRel: 'renders/hello.mp4' } },
      { id: 'b', type: 'comp', data: { luaPath: 'comps/hello.lua' } },
      { id: 'a', type: 'file', data: { path: 'assets/in/a.png', kind: 'image' } },
      { id: 'd', type: 'check', data: { findings: 'x' } },
    ]
    const edges: GraphEdge[] = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'b', target: 'd' },
    ]
    const lastRunHashes = contentHashes(nodes)
    const hashes = {
      ...lastRunHashes,
      a: 'changed',
    }

    expect(stale(nodes, edges, hashes, lastRunHashes)).toEqual(['b', 'c'])
  })

  test('draft to high quality marks the render stale', () => {
    const { nodes, edges } = fileCompRender()
    const lastRunHashes = contentHashes(nodes)
    const hashes = contentHashes([
      nodes[0]!,
      nodes[1]!,
      { ...nodes[2]!, data: { outputRel: 'renders/hello.mp4', quality: 'high' } },
    ])

    expect(stale(nodes, edges, hashes, lastRunHashes)).toEqual(['c'])
  })
})
