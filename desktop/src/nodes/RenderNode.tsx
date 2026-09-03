import { useContext, useEffect, useRef } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { connectedCompLua } from '../graph/connectedComp'
import type { RenderNodeData, RenderQuality } from '../graph/types'
import { StudioContext, formatError } from '../studioContext'

type JobSnapshot = {
  id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  stdout: string
  stderr: string
  output_rel?: string | null
  error?: string | null
}

const QUALITIES: RenderQuality[] = ['draft', 'standard', 'high']

export function RenderNode({ id, data }: NodeProps<Node<RenderNodeData, 'render'>>) {
  const studio = useContext(StudioContext)
  const studioRef = useRef(studio)
  studioRef.current = studio
  const quality: RenderQuality = data.quality ?? 'standard'
  const running = data.status === 'running'
  const stale = studio.isStale(id)

  useEffect(() => {
    const jobId = data.jobId
    if (!jobId) return
    let stopped = false
    let timer = 0
    const apply = (snap: JobSnapshot) => {
      if (stopped || snap.id !== jobId || snap.status === 'running') return
      stopped = true
      window.clearInterval(timer)
      const api = studioRef.current
      if (snap.status === 'succeeded') {
        api.patchNodeData(id, {
          status: 'succeeded',
          outputRel: snap.output_rel ?? undefined,
          stderr: '',
        })
        api.recordSuccessfulRun(id)
        api.setError(null)
        return
      }
      api.patchNodeData(id, {
        status: snap.status,
        stderr: snap.stderr || snap.error || snap.status,
      })
    }
    timer = window.setInterval(() => {
      void invoke<JobSnapshot>('job_status', { id: jobId })
        .then(apply)
        .catch(() => undefined)
    }, 800)
    const unlisten = listen<JobSnapshot>('job-done', (event) => {
      apply(event.payload)
    })
    return () => {
      stopped = true
      window.clearInterval(timer)
      void unlisten.then((fn) => fn())
    }
  }, [data.jobId, id])

  async function run() {
    if (!studio.projectPath) {
      studio.setError('Open a project first')
      return
    }
    const { nodes, edges } = studio.getGraph()
    const luaPath = connectedCompLua(id, nodes, edges)
    if (!luaPath) {
      studio.setError('Connect a Comp node to Render')
      studio.patchNodeData(id, { stderr: 'Connect a Comp node', status: 'failed' })
      return
    }
    try {
      studio.patchNodeData(id, { status: 'running', stderr: '', jobId: undefined })
      const jobId = await invoke<string>('start_render', {
        project: studio.projectPath,
        luaRel: luaPath,
        quality,
      })
      studio.patchNodeData(id, { jobId, status: 'running', stderr: '' })
      studio.setError(null)
    } catch (err) {
      const message = formatError(err)
      studio.patchNodeData(id, { status: 'failed', stderr: message })
      studio.setError(message)
    }
  }

  return (
    <div className="studio-node">
      <Handle type="target" position={Position.Left} id="comp" className="kind-comp" title="comp" />
      <Handle
        type="target"
        position={Position.Left}
        id="audio"
        className="kind-audio"
        title="audio (optional, multiple)"
        style={{ top: 42 }}
      />
      <Handle type="source" position={Position.Right} id="out" className="kind-video" title="video" />
      <strong>Render</strong>
      {stale ? <div className="studio-node-badge">stale</div> : null}
      <div className="studio-node-path">{data.outputRel ?? 'renders/<stem>.mp4'}</div>
      <label className="studio-node-quality">
        Quality
        <select
          className="nodrag"
          value={quality}
          disabled={running}
          onChange={(event) => {
            studio.patchNodeData(id, { quality: event.target.value as RenderQuality })
          }}
        >
          {QUALITIES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <button type="button" disabled={running} onClick={() => void run()}>
        {running ? 'Rendering…' : 'Run'}
      </button>
      {data.stderr ? <div className="studio-node-status">{data.stderr}</div> : null}
    </div>
  )
}
