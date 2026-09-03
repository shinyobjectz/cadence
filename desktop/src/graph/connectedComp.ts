import type { Edge, Node } from '@xyflow/react'
import type { CompNodeData } from './types'

/** Lua path of the Comp wired into a node's `comp` handle. */
export function connectedCompLua(nodeId: string, nodes: Node[], edges: Edge[]): string | null {
  for (const edge of edges) {
    if (edge.target !== nodeId) continue
    if (edge.targetHandle && edge.targetHandle !== 'comp') continue
    const source = nodes.find((n) => n.id === edge.source)
    if (source?.type !== 'comp') continue
    const luaPath = (source.data as CompNodeData).luaPath
    if (luaPath) return luaPath
  }
  return null
}

/** Lua path for a Check node: direct Comp, or Comp feeding a connected Render. */
export function connectedCheckLua(nodeId: string, nodes: Node[], edges: Edge[]): string | null {
  for (const edge of edges) {
    if (edge.target !== nodeId) continue
    const source = nodes.find((n) => n.id === edge.source)
    if (!source) continue
    if (source.type === 'comp') {
      const luaPath = (source.data as CompNodeData).luaPath
      if (luaPath) return luaPath
    }
    if (source.type === 'render') {
      const luaPath = connectedCompLua(source.id, nodes, edges)
      if (luaPath) return luaPath
    }
  }
  return null
}
