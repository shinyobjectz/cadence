import { useContext } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { invoke } from '@tauri-apps/api/core'
import { kindFromPath } from '../graph/fileKind'
import type { FileNodeData } from '../graph/types'
import { StudioContext, formatError } from '../studioContext'

export function FileNode({ id, data }: NodeProps<Node<FileNodeData, 'file'>>) {
  const studio = useContext(StudioContext)
  const kind = data.kind ?? 'file'

  async function pick() {
    if (!studio.projectPath) {
      studio.setError('Open a project first')
      return
    }
    try {
      const src = await invoke<string | null>('pick_file')
      if (!src) return
      const path = await invoke<string>('import_file', {
        project: studio.projectPath,
        src,
      })
      studio.patchNodeData(id, { path, kind: kindFromPath(path) })
      studio.setError(null)
    } catch (err) {
      studio.setError(formatError(err))
    }
  }

  return (
    <div className="studio-node">
      <Handle type="source" position={Position.Right} id="out" />
      <strong>File</strong>
      <div className="studio-node-path">{data.path ?? 'No file'}</div>
      <div className="studio-node-kind">{kind}</div>
      <button type="button" onClick={() => void pick()}>
        Pick…
      </button>
    </div>
  )
}
