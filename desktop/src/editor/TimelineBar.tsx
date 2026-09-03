import { useCallback, useEffect, useMemo, useRef } from 'react'
import { Pause, Play } from 'lucide-react'
import { Tip } from '../components/ui/tooltip'
import { cn } from '../lib/utils'
import { useEditor } from './store'
import { voEndTime } from './editorial'
import { collectTimelineMarkers, timeToPct, visibleRange } from './timeline'
import { formatTimecode } from './time'
import { VolumeFader } from './VolumeFader'

/** Bottom timeline: play + scrub + fader on one row; timecode in footer below. */
export function TimelineBar() {
  const doc = useEditor((s) => s.doc)
  const duration = doc.duration
  const fps = doc.fps
  const aspect = doc.aspect
  const playhead = useEditor((s) => s.playhead)
  const playing = useEditor((s) => s.playing)
  const zoom = useEditor((s) => s.zoom)
  const volume = useEditor((s) => s.volume)
  const setPlayhead = useEditor((s) => s.setPlayhead)
  const setScrubbing = useEditor((s) => s.setScrubbing)
  const setZoom = useEditor((s) => s.setZoom)
  const togglePlay = useEditor((s) => s.togglePlay)
  const setVolume = useEditor((s) => s.setVolume)

  const trackRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const markers = useMemo(() => collectTimelineMarkers(doc), [doc])
  const voEnd = useMemo(() => voEndTime(doc), [doc])
  const { viewStart, viewEnd, viewDuration } = useMemo(
    () => visibleRange(duration, playhead, zoom),
    [duration, playhead, zoom],
  )

  const scrubAt = useCallback(
    (clientX: number) => {
      const el = trackRef.current
      if (!el || viewDuration <= 0) return
      const rect = el.getBoundingClientRect()
      const x = Math.max(0, Math.min(clientX - rect.left, rect.width))
      setPlayhead(viewStart + (x / rect.width) * viewDuration)
    },
    [viewStart, viewDuration, setPlayhead],
  )

  useEffect(() => {
    const up = () => {
      dragging.current = false
      setScrubbing(false)
    }
    const move = (e: PointerEvent) => {
      if (!dragging.current) return
      scrubAt(e.clientX)
    }
    window.addEventListener('pointerup', up)
    window.addEventListener('pointermove', move)
    return () => {
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointermove', move)
    }
  }, [scrubAt, setScrubbing])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.code === 'Space' &&
        !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)
      ) {
        e.preventDefault()
        togglePlay()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [togglePlay])

  const playPct = timeToPct(playhead, viewStart, viewDuration)
  const voEndPct =
    voEnd > 0 && voEnd < duration ? timeToPct(voEnd, viewStart, viewDuration) : null
  const tailStartPct = voEndPct
  const tailWidthPct =
    tailStartPct != null
      ? timeToPct(duration, viewStart, viewDuration) - tailStartPct
      : 0
  const inEditorial = voEnd > 0 && playhead >= voEnd

  return (
    <div className="shrink-0 px-3 pb-3 pt-1">
      <div className={cn('rounded-xl bg-muted/40 ring-1 ring-border/50 shadow-sm')}>
        {/* Row 1: transport only */}
        <div className="flex items-center gap-3 px-3 py-2">
          <Tip label={playing ? 'Pause (Space)' : 'Play (Space)'}>
            <button
              type="button"
              onClick={togglePlay}
              className={cn(
                'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
                'text-foreground hover:bg-background/70 active:bg-background/90',
                playing && 'bg-background/90 shadow-sm',
              )}
              aria-label={playing ? 'Pause' : 'Play'}
            >
              {playing ? (
                <Pause size={16} fill="currentColor" strokeWidth={0} />
              ) : (
                <Play size={16} fill="currentColor" strokeWidth={0} className="ml-0.5" />
              )}
            </button>
          </Tip>

          <div
            ref={trackRef}
            className="relative h-9 min-w-0 flex-1 cursor-crosshair select-none overflow-hidden"
            onPointerDown={(e) => {
              dragging.current = true
              setScrubbing(true)
              scrubAt(e.clientX)
            }}
            onWheel={(e) => {
              e.preventDefault()
              const factor = e.deltaY > 0 ? 0.85 : 1.18
              setZoom(useEditor.getState().zoom * factor)
            }}
            role="slider"
            aria-valuemin={viewStart}
            aria-valuemax={viewEnd}
            aria-valuenow={playhead}
            aria-label="Timeline"
          >
            <div className="absolute left-0 right-0 top-1/2 h-px -translate-y-1/2 bg-border/80" />

            {tailStartPct != null && tailWidthPct > 0 ? (
              <div
                className="pointer-events-none absolute top-1 bottom-1 rounded-sm bg-muted/50"
                style={{ left: `${tailStartPct}%`, width: `${tailWidthPct}%` }}
                title="Editorial tail (post-VO)"
              />
            ) : null}

            {markers.map((m, i) => {
              const pct = timeToPct(m.t, viewStart, viewDuration)
              if (pct < -2 || pct > 102) return null
              return (
                <div
                  key={`${m.kind}-${m.t}-${i}`}
                  className="pointer-events-none absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
                  style={{ left: `${pct}%` }}
                >
                {m.kind === 'scene' ? (
                  <div className="h-2 w-0.5 rounded-full bg-accent/70" title={m.label} />
                ) : m.kind === 'voEnd' ? (
                  <div
                    className="h-3 w-px rounded-full bg-amber-500/80"
                    title={m.label}
                  />
                ) : m.kind === 'word' ? (
                  <div
                    className="size-0.5 rounded-full bg-accent/40"
                    title={m.label}
                  />
                ) : (
                  <div className="size-1 rounded-full bg-muted-foreground/50" />
                )}
                </div>
              )
            })}

            <div
              className="pointer-events-none absolute top-0 bottom-0 w-px bg-foreground"
              style={{ left: `${playPct}%` }}
            />
            <div
              className="pointer-events-none absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground ring-2 ring-background"
              style={{ left: `${playPct}%` }}
            />
          </div>

          <VolumeFader volume={volume} onChange={setVolume} className="shrink-0" />
        </div>

        {/* Footer: metadata below the scrub row */}
        <div className="flex items-center justify-between border-t border-border/30 px-3 py-1">
          <div className="flex items-center gap-1.5 tabular-nums text-[10px] text-muted-foreground">
            <span>{formatTimecode(playhead, fps)}</span>
            <span className="opacity-40">/</span>
            <span>{formatTimecode(duration, fps)}</span>
            {inEditorial ? (
              <span className="rounded bg-amber-500/15 px-1 py-0.5 text-[9px] text-amber-700 dark:text-amber-400">
                editorial
              </span>
            ) : voEndPct != null ? (
              <span className="text-[9px] opacity-50">VO {formatTimecode(voEnd, fps)}</span>
            ) : null}
          </div>
          <div className="flex items-center gap-2 text-[9px] text-muted-foreground">
            <span className="rounded bg-background/60 px-1 py-0.5 font-mono">{aspect}</span>
            <span className="font-mono opacity-60">{fps} fps</span>
          </div>
        </div>
      </div>
    </div>
  )
}
