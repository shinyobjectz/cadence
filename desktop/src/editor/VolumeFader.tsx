import { useCallback, useEffect, useRef } from 'react'
import { cn } from '../lib/utils'

type VolumeFaderProps = {
  volume: number
  onChange: (v: number) => void
  className?: string
}

/** Vertical desk-style fader with square level cap. */
export function VolumeFader({ volume, onChange, className }: VolumeFaderProps) {
  const trackRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const setFromPointer = useCallback(
    (clientY: number) => {
      const el = trackRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const y = Math.max(0, Math.min(clientY - rect.top, rect.height))
      const next = 1 - y / rect.height
      onChange(Math.max(0, Math.min(1, next)))
    },
    [onChange],
  )

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return
      setFromPointer(e.clientY)
    }
    const onUp = () => {
      dragging.current = false
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [setFromPointer])

  const pct = Math.round(volume * 100)

  return (
    <div className={cn('flex flex-col items-center gap-1', className)}>
      <div
        ref={trackRef}
        className="relative h-16 w-3 cursor-ns-resize rounded-sm bg-background/80 ring-1 ring-border/70"
        onPointerDown={(e) => {
          dragging.current = true
          ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
          setFromPointer(e.clientY)
        }}
        role="slider"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="Volume"
        aria-orientation="vertical"
      >
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 rounded-sm bg-accent/35"
          style={{ height: `${pct}%` }}
        />
        <div
          className="pointer-events-none absolute left-1/2 h-2 w-2.5 -translate-x-1/2 rounded-[2px] bg-foreground shadow-sm ring-1 ring-background"
          style={{ bottom: `calc(${pct}% - 4px)` }}
        />
      </div>
      <span className="text-[9px] tabular-nums text-muted-foreground">{pct}</span>
    </div>
  )
}
