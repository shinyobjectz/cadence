import { useContext, useEffect, useRef } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { connectedCheckLua } from '../graph/connectedComp'
import type { CheckMode, CheckNodeData } from '../graph/types'
import { formatFindingsBlock, parseCadenceJson } from '../cadence/findings'
import { StudioContext, formatError } from '../studioContext'

type JobSnapshot = {
  id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  stdout: string
  stderr: string
  output_rel?: string | null
  error?: string | null
}

const MODES: CheckMode[] = ['lint', 'check', 'hash']

export function CheckNode({ id, data }: NodeProps<Node<CheckNodeData, 'check'>>) {
  const studio = useContext(StudioContext)
  const studioRef = useRef(studio)
  studioRef.current = studio
  const mode: CheckMode = data.mode ?? 'lint'
  const running = data.status === 'running'
  const failed = data.status === 'failed'

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
        const parsed = parseCadenceJson(snap.stdout || '')
        api.patchNodeData(id, {
          status: 'succeeded',
          output: parsed ? formatFindingsBlock(parsed) : snap.stdout || 'ok',
          findings: parsed?.findings,
          result: parsed,
        })
        api.setError(null)
        return
      }
      const parsed = parseCadenceJson(snap.stdout || snap.stderr || '')
      api.patchNodeData(id, {
        status: snap.status,
        output: parsed
          ? formatFindingsBlock(parsed)
          : snap.stderr || snap.stdout || snap.error || snap.status,
        findings: parsed?.findings,
        result: parsed,
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
    const luaPath = connectedCheckLua(id, nodes, edges)
    if (!luaPath) {
      studio.setError('Connect a Comp or Render node to Check')
      studio.patchNodeData(id, {
        output: 'Connect a Comp or Render node',
        status: 'failed',
      })
      return
    }
    try {
      studio.patchNodeData(id, { status: 'running', output: '', jobId: undefined })
      const jobId = await invoke<string>('start_check', {
        project: studio.projectPath,
        luaRel: luaPath,
        mode,
      })
      studio.patchNodeData(id, { jobId, status: 'running', output: '' })
      studio.setError(null)
    } catch (err) {
      const message = formatError(err)
      studio.patchNodeData(id, { status: 'failed', output: message })
      studio.setError(message)
    }
  }

  return (
    <div className={`studio-node${failed ? ' studio-node-failed' : ''}`}>
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="kind-check"
        title="comp or video"
      />
      <strong>Check</strong>
      <label className="studio-node-quality">
        Mode
        <select
          className="nodrag"
          value={mode}
          disabled={running}
          onChange={(event) => {
            studio.patchNodeData(id, { mode: event.target.value as CheckMode })
          }}
        >
          {MODES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <button type="button" disabled={running} onClick={() => void run()}>
        {running ? 'Checking…' : 'Run'}
      </button>
      {data.output ? <div className="studio-node-status">{data.output}</div> : null}
      {data.findings?.length ? (
        <ul className="studio-node-findings nodrag">
          {data.findings.slice(0, 6).map((f, i) => (
            <li key={`${f.code}-${i}`} className={`finding-${f.severity}`}>
              <code>{f.code}</code> {f.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
