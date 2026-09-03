import { createContext } from 'react'
import type { Edge, Node } from '@xyflow/react'

export function formatError(err: unknown): string {
  if (typeof err === 'string') return err
  if (err instanceof Error) return err.message
  return String(err)
}

export type StudioApi = {
  projectPath: string | null
  setError: (msg: string | null) => void
  patchNodeData: (id: string, data: Record<string, unknown>) => void
  getGraph: () => { nodes: Node[]; edges: Edge[] }
  isStale: (id: string) => boolean
  recordSuccessfulRun: (id: string) => void
}

export const StudioContext = createContext<StudioApi>({
  projectPath: null,
  setError: () => undefined,
  patchNodeData: () => undefined,
  getGraph: () => ({ nodes: [], edges: [] }),
  isStale: () => false,
  recordSuccessfulRun: () => undefined,
})
