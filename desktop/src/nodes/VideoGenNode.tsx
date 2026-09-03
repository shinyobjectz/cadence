import { useContext } from 'react'
import { convertFileSrc } from '@tauri-apps/api/core'
import { Handle, Position, type Edge, type Node, type NodeProps } from '@xyflow/react'
import { costChip, generateVideo, VIDEO_MODELS } from '../ai/gateway'
import type { TextNodeData, VideoGenNodeData } from '../graph/types'
import { StudioContext, formatError } from '../studioContext'

function connectedPrompt(nodeId: string, nodes: Node[], edges: Edge[]): string | null {
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

function thumbSrc(projectPath: string | null, rel: string | undefined): string | null {
  if (!projectPath || !rel) return null
  const abs = `${projectPath.replace(/\\/g, '/')}/${rel.replace(/\\/g, '/')}`
  return convertFileSrc(abs)
}

export function VideoGenNode({ id, data }: NodeProps<Node<VideoGenNodeData, 'video-gen'>>) {
  const studio = useContext(StudioContext)
  const model = data.model ?? VIDEO_MODELS[0]!.id
  const selected = VIDEO_MODELS.find((item) => item.id === model) ?? VIDEO_MODELS[0]!
  const running = data.status === 'running'
  const thumb = thumbSrc(studio.projectPath, data.outputRel)

  async function run() {
    if (!studio.projectPath) {
      studio.setError('Open a project first')
      return
    }
    const { nodes, edges } = studio.getGraph()
    const prompt = connectedPrompt(id, nodes, edges) || data.prompt?.trim() || ''
    if (!prompt) {
      studio.setError('Enter a prompt or connect a Text node')
      return
    }
    try {
      studio.patchNodeData(id, { status: 'running', error: '' })
      const outputRel = await generateVideo(studio.projectPath, prompt, model)
      studio.patchNodeData(id, { status: 'succeeded', outputRel, error: '' })
      studio.setError(null)
    } catch (err) {
      const message = formatError(err)
      studio.patchNodeData(id, { status: 'failed', error: message })
      studio.setError(message)
    }
  }

  return (
    <div className="studio-node">
      <Handle type="target" position={Position.Left} id="text" className="kind-text" title="text" />
      <Handle
        type="source"
        position={Position.Right}
        id="video"
        className="kind-video"
        title="video"
      />
      <strong>Video</strong>
      <div className="studio-node-path">{data.outputRel ?? 'assets/in/<stem>-<hash>.mp4'}</div>
      <label className="studio-node-quality">
        Model
        <select
          className="nodrag"
          value={model}
          disabled={running}
          onChange={(event) => studio.patchNodeData(id, { model: event.target.value })}
        >
          {VIDEO_MODELS.map((item) => (
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
        placeholder="Prompt (or connect Text)"
        value={data.prompt ?? ''}
        onChange={(event) => studio.patchNodeData(id, { prompt: event.target.value })}
      />
      {thumb ? (
        <video className="studio-node-thumb" src={thumb} muted playsInline />
      ) : null}
      <button type="button" disabled={running} onClick={() => void run()}>
        {running ? 'Generating…' : 'Run'}
      </button>
      {data.error ? <div className="studio-node-status">{data.error}</div> : null}
    </div>
  )
}
