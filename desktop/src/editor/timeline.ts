import { voEndTime } from './editorial'
import type { ProjectDoc } from './types'
import { keyframeTime } from './time'

export type TimelineMarker = {
  t: number
  kind: 'scene' | 'keyframe' | 'word' | 'voEnd'
  label?: string
}

export function collectTimelineMarkers(doc: ProjectDoc): TimelineMarker[] {
  const markers: TimelineMarker[] = []
  const voEnd = voEndTime(doc)
  if (voEnd > 0) {
    markers.push({ t: voEnd, kind: 'voEnd', label: 'VO end' })
  }
  for (const scene of doc.scenes) {
    markers.push({ t: scene.at, kind: 'scene', label: 'Scene' })
  }
  for (const kf of doc.lineKeyframes) {
    const line = doc.lines.find((l) => l.id === kf.parentId)
    if (!line) continue
    markers.push({
      t: keyframeTime(kf.kind, line.words, kf.gapIndex),
      kind: 'keyframe',
    })
  }
  for (const line of doc.lines) {
    for (const word of line.words) {
      markers.push({ t: word.start, kind: 'word', label: word.text })
    }
  }
  return markers.sort((a, b) => a.t - b.t)
}

export function visibleRange(duration: number, playhead: number, zoom: number) {
  const viewDuration = Math.max(duration / zoom, 0.25)
  let viewStart = playhead - viewDuration / 2
  if (viewStart < 0) viewStart = 0
  if (viewStart + viewDuration > duration) viewStart = Math.max(0, duration - viewDuration)
  const viewEnd = Math.min(duration, viewStart + viewDuration)
  return { viewStart, viewEnd, viewDuration: viewEnd - viewStart }
}

export function timeToPct(t: number, viewStart: number, viewSpan: number) {
  if (viewSpan <= 0) return 0
  return ((t - viewStart) / viewSpan) * 100
}
