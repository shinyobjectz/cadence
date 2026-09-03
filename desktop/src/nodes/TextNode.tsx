import { useContext, useEffect, useRef, useState } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { listen } from '@tauri-apps/api/event'
import { costChip, generateText, TEXT_MODELS } from '../ai/gateway'
import type { TextNodeData } from '../graph/types'
import { StudioContext, formatError } from '../studioContext'

type JobSnapshot = {
  id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  stdout: string
  stderr: string
  error?: string | null
}

type GenTextEvent = { jobId: string; delta: string }

export function TextNode({ id, data }: NodeProps<Node<TextNodeData, 'text'>>) {
  const studio = useContext(StudioContext)
  const studioRef = useRef(studio)
  studioRef.current = studio
  const model = data.model ?? TEXT_MODELS[0]!.id
  const selected = TEXT_MODELS.find((item) => item.id === model) ?? TEXT_MODELS[0]!
  const running = data.status === 'running'
  const [stream, setStream] = useState('')

  useEffect(() => {
    const jobId = data.jobId
    if (!jobId) return
    let stopped = false
    const unlistenDelta = listen<GenTextEvent>('gen-text', (event) => {
      if (stopped || event.payload.jobId !== jobId) return
      setStream((prev) => prev + event.payload.delta)
    })
    const unlistenDone = listen<JobSnapshot>('job-done', (event) => {
      if (stopped || event.payload.id !== jobId) return
      stopped = true
      const api = studioRef.current
      if (event.payload.status === 'succeeded') {
        api.patchNodeData(id, {
          status: 'succeeded',
          text: event.payload.stdout,
          error: '',
        })
        api.setError(null)
        setStream('')
        return
      }
      api.patchNodeData(id, {
        status: event.payload.status,
        error: event.payload.stderr || event.payload.error || event.payload.status,
      })
      api.setError(event.payload.stderr || event.payload.error || event.payload.status)
      setStream('')
    })
    return () => {
      stopped = true
      void unlistenDelta.then((fn) => fn())
      void unlistenDone.then((fn) => fn())
    }
  }, [data.jobId, id])

  async function run() {
    const prompt = data.prompt?.trim() ?? ''
    if (!prompt) {
      studio.setError('Enter a prompt')
      return
    }
    setStream('')
    try {
      studio.patchNodeData(id, { status: 'running', error: '', jobId: undefined })
      const jobId = await generateText(prompt, model)
      studio.patchNodeData(id, { jobId, status: 'running', error: '' })
      studio.setError(null)
    } catch (err) {
      const message = formatError(err)
      studio.patchNodeData(id, { status: 'failed', error: message })
      studio.setError(message)
    }
  }

  const shown = running ? stream || data.text : data.text

  return (
    <div className="studio-node">
      <Handle type="source" position={Position.Right} id="text" className="kind-text" title="text" />
      <strong>Text</strong>
      <label className="studio-node-quality">
        Model
        <select
          className="nodrag"
          value={model}
          disabled={running}
          onChange={(event) => studio.patchNodeData(id, { model: event.target.value })}
        >
          {TEXT_MODELS.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        <span className="studio-cost" title="relative cost">
          {costChip(selected.cost)}
        </span>
      </label>
      <textarea
        className="nodrag studio-node-prompt"
        rows={3}
        disabled={running}
        placeholder="Prompt"
        value={data.prompt ?? ''}
        onChange={(event) => studio.patchNodeData(id, { prompt: event.target.value })}
      />
      <button type="button" disabled={running} onClick={() => void run()}>
        {running ? 'Generating…' : 'Run'}
      </button>
      {running ? <div className="studio-node-kind">streaming</div> : null}
      {shown ? <div className="studio-node-stream">{shown}</div> : null}
      {data.error ? <div className="studio-node-status">{data.error}</div> : null}
    </div>
  )
}
