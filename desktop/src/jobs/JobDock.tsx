import { useEffect, useState } from 'react'
import { invoke, isTauri } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'

type JobSnapshot = {
  id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  stdout: string
  stderr: string
  output_rel?: string | null
  error?: string | null
}

type JobLog = {
  id: string
  stream: string
  line: string
}

function tail(text: string, lines = 12): string {
  const parts = text.split('\n').filter((line) => line.length > 0)
  return parts.slice(-lines).join('\n')
}

export function JobDock({
  onRunDirty,
  dirtyRenderCount,
  hasProject,
}: {
  onRunDirty?: () => void
  dirtyRenderCount?: number
  hasProject?: boolean
}) {
  const [jobs, setJobs] = useState<JobSnapshot[]>([])
  const [logs, setLogs] = useState<Record<string, JobLog[]>>({})
  const canRunDirty = Boolean(hasProject && dirtyRenderCount && dirtyRenderCount > 0)

  useEffect(() => {
    if (!isTauri()) return
    let cancelled = false
    const refresh = () => {
      void invoke<JobSnapshot[]>('list_jobs')
        .then((next) => {
          if (!cancelled) setJobs(next)
        })
        .catch(() => undefined)
    }
    refresh()
    const timer = window.setInterval(refresh, 800)
    const unlistenLog = listen<JobLog>('job-log', (event) => {
      const log = event.payload
      setLogs((prev) => {
        const existing = prev[log.id] ?? []
        return { ...prev, [log.id]: [...existing, log].slice(-80) }
      })
    })
    const unlistenDone = listen<JobSnapshot>('job-done', () => {
      refresh()
    })
    return () => {
      cancelled = true
      window.clearInterval(timer)
      void unlistenLog.then((fn) => fn())
      void unlistenDone.then((fn) => fn())
    }
  }, [])

  async function cancel(id: string) {
    try {
      await invoke('cancel_job', { id })
    } catch {
      /* dock still polls */
    }
  }

  return (
    <aside className="job-dock" aria-label="Jobs">
      <div className="job-dock-toolbar">
        <button type="button" disabled={!canRunDirty} onClick={() => onRunDirty?.()}>
          Run all dirty renders
        </button>
      </div>
      {jobs.length === 0 ? (
        <div className="job-dock-empty">No jobs</div>
      ) : (
        jobs.map((job) => {
          const streamed = (logs[job.id] ?? [])
            .map((log) => `${log.stream}: ${log.line}`)
            .join('\n')
          const fallback = [job.stderr, job.stdout, job.error].filter(Boolean).join('\n')
          return (
            <div key={job.id} className="job-dock-row">
              <div className="job-dock-meta">
                <span className={`job-dock-status status-${job.status}`}>{job.status}</span>
                <span className="job-dock-id">{job.id}</span>
                {job.output_rel ? <span className="job-dock-out">{job.output_rel}</span> : null}
                {job.status === 'running' ? (
                  <button type="button" onClick={() => void cancel(job.id)}>
                    Cancel
                  </button>
                ) : null}
              </div>
              <pre className="job-dock-log">{tail(streamed || fallback)}</pre>
            </div>
          )
        })
      )}
    </aside>
  )
}
