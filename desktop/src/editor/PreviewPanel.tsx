import { useEffect, useMemo, useRef } from 'react'
import { Lasso, MessageCircle, MousePointer2, Type } from 'lucide-react'
import { Tip } from '../components/ui/tooltip'
import { cn } from '../lib/utils'
import { useElluaPreview } from '../preview/useElluaPreview'
import { useVoPlayback } from '../preview/useVoPlayback'
import { useEditor } from './store'

/** Canvas preview — wasmoon comp + MediaBag video decode. */
export function PreviewViewport() {
  const doc = useEditor((s) => s.doc)
  const projectPath = useEditor((s) => s.projectPath)
  const playhead = useEditor((s) => s.playhead)
  const playing = useEditor((s) => s.playing)
  const scrubbing = useEditor((s) => s.scrubbing)
  const volume = useEditor((s) => s.volume)
  const setPlayhead = useEditor((s) => s.setPlayhead)
  const docDuration = doc.duration
  const aspect = doc.aspect

  const { canvasRef, meta, status, ready, drawFrame, pauseMedia } = useElluaPreview(
    doc.compPath,
    projectPath,
    doc,
  )
  const { voEnd, hasVo, pause: pauseVo } = useVoPlayback({
    projectPath,
    doc,
    playhead,
    playing,
    scrubbing,
    volume,
    setPlayhead,
  })
  const raf = useRef<number | null>(null)
  const last = useRef<number | null>(null)

  const previewT = useMemo(() => {
    if (meta.duration <= 0) return 0
    if (Math.abs(meta.duration - docDuration) < 0.01) return playhead
    return playhead % meta.duration
  }, [playhead, meta.duration, docDuration])

  useEffect(() => {
    if (!ready) return
    drawFrame(previewT, playing, volume)
  }, [ready, previewT, playing, volume, drawFrame])

  useEffect(() => {
    if (!playing) {
      if (raf.current) cancelAnimationFrame(raf.current)
      last.current = null
      pauseMedia()
      pauseVo()
      return
    }

    const tick = (now: number) => {
      if (last.current != null) {
        const dt = (now - last.current) / 1000
        const state = useEditor.getState()
        const inVo = hasVo && state.playhead < voEnd
        // VO region: playhead is driven by useVoPlayback from audio.currentTime.
        if (!inVo) {
          const next = state.playhead + dt
          if (next >= state.doc.duration) {
            state.setPlayhead(state.doc.duration)
            state.setPlaying(false)
            return
          }
          state.setPlayhead(next)
        }
      }
      last.current = now
      raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current)
      last.current = null
    }
  }, [playing, pauseMedia, pauseVo, hasVo, voEnd])

  const ratio = aspect === '9:16' ? 9 / 16 : 16 / 9

  return (
    <div className="flex flex-1 min-h-0 items-center justify-center p-6">
      <div
        className="relative max-h-full max-w-full overflow-hidden rounded-lg bg-black ring-1 ring-border/25"
        style={{
          aspectRatio: String(ratio),
          height: aspect === '9:16' ? 'min(72vh, 640px)' : undefined,
          width: aspect === '16:9' ? 'min(92%, 960px)' : undefined,
        }}
      >
        <canvas ref={canvasRef} className="h-full w-full object-contain" />
        {!ready && status ? (
          <div className="absolute inset-0 flex items-center justify-center bg-muted/20 text-[11px] text-muted-foreground">
            {status}
          </div>
        ) : null}
      </div>
    </div>
  )
}

export function PreviewPanel() {
  return (
    <div className="flex h-full min-w-0 flex-col bg-background">
      <PreviewViewport />
      <ToolToolbar />
    </div>
  )
}

const TOOL_ICONS = {
  select: MousePointer2,
  lasso: Lasso,
  comment: MessageCircle,
  text: Type,
} as const

function ToolToolbar() {
  const tool = useEditor((s) => s.tool)
  const setTool = useEditor((s) => s.setTool)

  const tools = [
    { id: 'select' as const, label: 'Select (V)' },
    { id: 'lasso' as const, label: 'Lasso (L)' },
    { id: 'comment' as const, label: 'Comment (C)' },
    { id: 'text' as const, label: 'Text (T)' },
  ]

  return (
    <div className="flex justify-center px-4 pb-3">
      <div
        className={cn(
          'inline-flex items-center gap-px rounded-[10px] px-0.5 py-0.5',
          'bg-muted/60 shadow-sm ring-1 ring-border/50 backdrop-blur-md',
        )}
      >
        {tools.map((t) => (
          <ToolbarBtn
            key={t.id}
            active={tool === t.id}
            label={t.label}
            onClick={() => setTool(t.id)}
          >
            {(() => {
              const Icon = TOOL_ICONS[t.id]
              return <Icon size={15} strokeWidth={1.75} />
            })()}
          </ToolbarBtn>
        ))}
      </div>
    </div>
  )
}

function ToolbarBtn({
  children,
  label,
  active,
  disabled,
  onClick,
}: {
  children: React.ReactNode
  label: string
  active?: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <Tip label={label}>
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        className={cn(
          'flex h-7 w-7 items-center justify-center rounded-md',
          'text-muted-foreground transition-colors',
          'hover:bg-background/70 hover:text-foreground',
          'disabled:pointer-events-none disabled:opacity-30',
          active && 'bg-background/90 text-foreground shadow-sm',
        )}
      >
        {children}
      </button>
    </Tip>
  )
}
