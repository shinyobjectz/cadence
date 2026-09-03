import type { GraphEdge, GraphNode, HashMap } from './types'

function adjacency(nodes: GraphNode[], edges: GraphEdge[]) {
  const ids = nodes.map((n) => n.id)
  const idSet = new Set(ids)
  const outgoing = new Map<string, string[]>(ids.map((id) => [id, []]))
  const incoming = new Map<string, string[]>(ids.map((id) => [id, []]))
  const indegree = new Map<string, number>(ids.map((id) => [id, 0]))

  for (const edge of edges) {
    if (!idSet.has(edge.source) || !idSet.has(edge.target)) continue
    outgoing.get(edge.source)!.push(edge.target)
    incoming.get(edge.target)!.push(edge.source)
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1)
  }

  return { ids, outgoing, incoming, indegree }
}

/** Kahn topological order. Throws if the graph has a cycle. */
export function plan(nodes: GraphNode[], edges: GraphEdge[]): string[] {
  const { ids, outgoing, indegree } = adjacency(nodes, edges)
  const queue = ids.filter((id) => indegree.get(id) === 0)
  const order: string[] = []

  while (queue.length > 0) {
    const id = queue.shift()!
    order.push(id)
    for (const next of outgoing.get(id) ?? []) {
      const nextDegree = (indegree.get(next) ?? 0) - 1
      indegree.set(next, nextDegree)
      if (nextDegree === 0) queue.push(next)
    }
  }

  if (order.length !== ids.length) {
    throw new Error('cycle')
  }
  return order
}

/**
 * Node ids that need a re-run because an upstream output hash changed
 * relative to `lastRunHashes`. Returns ids in topo order.
 * Throws `'cycle'` if the graph is cyclic.
 */
export function dirty(
  nodes: GraphNode[],
  edges: GraphEdge[],
  hashes: HashMap,
  lastRunHashes: HashMap,
): string[] {
  const order = plan(nodes, edges)
  const { incoming } = adjacency(nodes, edges)
  const changed = (id: string) => hashes[id] !== lastRunHashes[id]
  const dirtyIds = new Set<string>()

  for (const id of order) {
    const parents = incoming.get(id) ?? []
    if (parents.some((parent) => changed(parent) || dirtyIds.has(parent))) {
      dirtyIds.add(id)
    }
  }

  return order.filter((id) => dirtyIds.has(id))
}

function field(data: Record<string, unknown> | undefined, key: string): string {
  const value = data?.[key]
  return typeof value === 'string' ? value : ''
}

function captureOutputRels(data: Record<string, unknown> | undefined): string {
  const outputs = data?.outputs
  if (!Array.isArray(outputs)) return ''
  return outputs
    .map((item) => {
      if (!item || typeof item !== 'object') return ''
      const rel = (item as { rel?: unknown }).rel
      return typeof rel === 'string' ? rel : ''
    })
    .join('|')
}

function nodeContentHash(node: GraphNode): string | undefined {
  const data = node.data
  switch (node.type) {
    case 'file':
      return JSON.stringify([field(data, 'path'), field(data, 'kind')])
    case 'image-gen':
    case 'video-gen':
      return JSON.stringify([field(data, 'outputRel'), field(data, 'prompt')])
    case 'tts':
      return JSON.stringify([field(data, 'outputRel'), field(data, 'prompt'), field(data, 'text')])
    case 'capture':
      return captureOutputRels(data)
    case 'comp':
      return field(data, 'luaPath')
    case 'render':
      return JSON.stringify([field(data, 'outputRel'), field(data, 'quality') || 'standard'])
    default:
      return undefined
  }
}

/** Stable content hashes from node data so File/gen/tts/capture/comp/render edits are visible. */
export function contentHashes(nodes: GraphNode[]): Record<string, string> {
  const hashes: Record<string, string> = {}
  for (const node of nodes) {
    const hash = nodeContentHash(node)
    if (hash !== undefined) hashes[node.id] = hash
  }
  return hashes
}

/**
 * Dirty Comp/Render node ids in topo order, including a Comp/Render whose own
 * hash changed (e.g. draft→high quality) so it shows stale.
 */
export function stale(
  nodes: GraphNode[],
  edges: GraphEdge[],
  hashes: HashMap,
  lastRunHashes: HashMap,
): string[] {
  const types = new Map(nodes.map((node) => [node.id, node.type]))
  const dirtyIds = new Set(dirty(nodes, edges, hashes, lastRunHashes))
  return plan(nodes, edges).filter((id) => {
    const type = types.get(id)
    if (type !== 'comp' && type !== 'render') return false
    return dirtyIds.has(id) || hashes[id] !== lastRunHashes[id]
  })
}
