import type { Edge, Node, Viewport } from '@xyflow/react'

export type StudioFile = {
  version: 1
  name: string
  viewport: Viewport
  nodes: Node[]
  edges: Edge[]
  lastRunHashes?: Record<string, string>
}
