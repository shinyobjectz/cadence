import { useContext, useEffect, useRef } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { Handle, Position, type Edge, type Node, type NodeProps } from '@xyflow/react'
import type { TextNodeData, TtsKind, TtsNodeData } from '../graph/types'
import { StudioContext, formatError } from '../studioContext'

type JobSnapshot = {
  id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  stdout: string
  stderr: string
  output_rel?: string | null
  error?: string | null
}

const KINDS: { id: TtsKind; label: string }[] = [
  { id: 'tts', label: 'TTS' },
  { id: 'sfx', label: 'SFX' },
  { id: 'music', label: 'Music' },
]

function connectedText(nodeId: string, nodes: Node[], edges: Edge[]): string | null {
  for (const edge of edges) {
    if (edge.target !== nodeId) continue
    if (edge.targetHandle && edge.targetHandle !== 'text') continue
    const source = nodes.find((n) => n.id === edge.source)
    if (source?.type !== 'text') continue
    const text = (source.data as TextNodeData).text?.trim()
    if (text) return text
  }
  return null
}

export function TtsNode({ id, data }: NodeProps<Node<TtsNodeData, 'tts'>>) {
  const studio = useContext(StudioContext)
  const studioRef = useRef(studio)
  studioRef.current = studio
  const kind: TtsKind = data.kind ?? 'tts'
  const running = data.status === 'running'

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
      if (snap.status !== 'succeeded') {
        const message = snap.stderr || snap.error || snap.status
        api.patchNodeData(id, { status: snap.status, error: message })
        api.setError(message)
        return
      }
      if (!api.projectPath) {
        api.patchNodeData(id, { status: 'failed', error: 'Open a project first' })
        return
      }
      const nodeData = api.getGraph().nodes.find((n) => n.id === id)?.data as
        | TtsNodeData
        | undefined
      void invoke<string>('import_tts_cache', {
        project: api.projectPath,
        kind: nodeData?.kind ?? 'tts',
        text: nodeData?.text ?? '',
      })
        .then((outputRel) => {
          if (stopped) return
          api.patchNodeData(id, { status: 'succeeded', outputRel, error: '' })
          api.setError(null)
        })
        .catch((err: unknown) => {
          if (stopped) return
          const message = formatError(err)
          api.patchNodeData(id, { status: 'failed', error: message })
          api.setError(message)
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
    const text = connectedText(id, nodes, edges) || data.text?.trim() || ''
    if (!text) {
      studio.setError('Enter text or connect a Text node')
      return
    }
    try {
      studio.patchNodeData(id, { status: 'running', error: '', jobId: undefined, text })
      const jobId = await invoke<string>('start_tts', {
        project: studio.projectPath,
        nodeId: id,
        kind,
        text,
      })
      studio.patchNodeData(id, { jobId, status: 'running', error: '' })
      studio.setError(null)
    } catch (err) {
      const message = formatError(err)
      studio.patchNodeData(id, { status: 'failed', error: message })
      studio.setError(message)
    }
  }

  const placeholder =
    kind === 'tts' ? 'Text (or connect Text)' : 'Prompt (or connect Text)'

  return (
    <div className="studio-node">
      <Handle type="target" position={Position.Left} id="text" className="kind-text" title="text" />
      <Handle
        type="source"
        position={Position.Right}
        id="audio"
        className="kind-audio"
        title="audio"
      />
      <strong>{KINDS.find((item) => item.id === kind)?.label ?? 'TTS'}</strong>
      <div className="studio-node-path">{data.outputRel ?? 'assets/in/<kind>-<hash>.mp3'}</div>
      <label className="studio-node-quality">
        Kind
        <select
          className="nodrag"
          value={kind}
          disabled={running}
          onChange={(event) =>
            studio.patchNodeData(id, { kind: event.target.value as TtsKind })
          }
        >
          {KINDS.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <textarea
        className="nodrag studio-node-prompt"
        rows={3}
        disabled={running}
        placeholder={placeholder}
        value={data.text ?? ''}
        onChange={(event) => studio.patchNodeData(id, { text: event.target.value })}
      />
      <button type="button" disabled={running} onClick={() => void run()}>
        {running ? 'Resolving…' : 'Run'}
      </button>
      {data.error ? <div className="studio-node-status">{data.error}</div> : null}
    </div>
  )
}
