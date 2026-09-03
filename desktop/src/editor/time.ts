import type { KeyframeKind } from './types'

export function formatTimecode(seconds: number, fps = 30): string {
  const totalFrames = Math.max(0, Math.floor(seconds * fps + 0.5))
  const f = totalFrames % fps
  const totalSec = Math.floor(totalFrames / fps)
  const s = totalSec % 60
  const m = Math.floor(totalSec / 60) % 60
  const h = Math.floor(totalSec / 3600)
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}:${pad(f, 2)}`
  return `${pad(m)}:${pad(s)}:${pad(f, 2)}`
}

function pad(n: number, w = 2) {
  return String(n).padStart(w, '0')
}

/** Resolve keyframe time from word boundaries. */
export function keyframeTime(
  kind: KeyframeKind,
  words: { start: number; end: number }[],
  gapIndex: number,
): number {
  if (words.length === 0) return 0
  if (gapIndex <= 0) {
    return kind === 'close' ? words[0]!.start : words[0]!.start
  }
  if (gapIndex >= words.length) {
    const last = words[words.length - 1]!
    return kind === 'open' ? last.end : last.end
  }
  const left = words[gapIndex - 1]!
  const right = words[gapIndex]!
  if (kind === 'open') return right.start
  if (kind === 'close') return left.end
  return (left.end + right.start) / 2
}
