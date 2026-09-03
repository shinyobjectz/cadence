import { useContext, useEffect, useRef } from 'react'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import type { CaptureAsset, CaptureNodeData } from '../graph/types'
import { StudioContext, formatError } from '../studioContext'

type JobSnapshot = {
  id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  stdout: string
  stderr: string
  output_rel?: string | null
  error?: string | null
}

function thumbSrc(projectPath: string | null, rel: string | undefined): string | null {
  if (!projectPath || !rel) return null
  const abs = `${projectPath.replace(/\\/g, '/')}/${rel.replace(/\\/g, '/')}`
  return convertFileSrc(abs)
}

export function CaptureNode({ id, data }: NodeProps<Node<CaptureNodeData, 'capture'>>) {
  const studio = useContext(StudioContext)
  const studioRef = useRef(studio)
  studioRef.current = studio
  const running = data.status === 'running'
  const outputs = data.outputs ?? []
  const page = outputs.find((asset) => asset.id === 'page')
  const thumb = thumbSrc(studio.projectPath, page?.rel)

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
      const rel = snap.output_rel
      if (!rel || !api.projectPath) {
        api.patchNodeData(id, { status: 'failed', error: 'capture produced no output path' })
        return
      }
      void invoke<CaptureAsset[]>('list_capture_outputs', {
        project: api.projectPath,
        rel,
      })
        .then((listed) => {
          if (stopped) return
          api.patchNodeData(id, {
            status: 'succeeded',
            host: rel.replace(/^assets\/capture\//, ''),
            outputs: listed,
            error: '',
          })
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
    const url = data.url?.trim() || 'https://example.com'
    try {
      studio.patchNodeData(id, { status: 'running', error: '', jobId: undefined, url })
      const jobId = await invoke<string>('start_capture', {
        project: studio.projectPath,
        url,
      })
      studio.patchNodeData(id, { jobId, status: 'running', error: '' })
      studio.setError(null)
    } catch (err) {
      const message = formatError(err)
      studio.patchNodeData(id, { status: 'failed', error: message })
      studio.setError(message)
    }
  }

  return (
    <div className="studio-node">
      {outputs.map((asset, index) => (
        <Handle
          key={asset.id}
          type="source"
          position={Position.Right}
          id={asset.id}
          className={`kind-${asset.kind}`}
          style={{ top: 28 + index * 16 }}
          title={`${asset.kind}: ${asset.rel}`}
        />
      ))}
      <strong>Capture</strong>
      <div className="studio-node-path">
        {data.host ? `assets/capture/${data.host}` : 'assets/capture/<host>'}
      </div>
      <input
        className="nodrag studio-node-prompt"
        type="text"
        disabled={running}
        placeholder="https://example.com"
        value={data.url ?? ''}
        onChange={(event) => studio.patchNodeData(id, { url: event.target.value })}
      />
      {thumb ? <img className="studio-node-thumb" src={thumb} alt="" /> : null}
      {outputs.length > 0 ? (
        <ul className="studio-node-inputs">
          {outputs.map((asset) => (
            <li key={asset.id}>
              {asset.kind}: {asset.rel.split('/').pop()}
            </li>
          ))}
        </ul>
      ) : null}
      <button type="button" disabled={running} onClick={() => void run()}>
        {running ? 'Capturing…' : 'Run'}
      </button>
      {data.error ? <div className="studio-node-status">{data.error}</div> : null}
    </div>
  )
}
