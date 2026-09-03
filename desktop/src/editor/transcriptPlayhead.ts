import { activeSceneAt, voEndTime } from './editorial'
import type { ProjectDoc, Word } from './types'
import { keyframeTime } from './time'

export type TranscriptPlayhead =
  | { kind: 'word'; lineId: string; wordId: string; word: Word }
  | { kind: 'keyframe'; lineId: string; keyframeId: string }
  | { kind: 'gap'; lineId: string; gapIndex: number }
  /** VO finished — playhead is in montage / hold / end-card editorial time. */
  | { kind: 'editorial'; sceneId: string | null }
  | null

const KEYFRAME_EPS = 0.04

/** Map timeline playhead → transcript position for highlight / caret. */
export function transcriptPlayheadAt(doc: ProjectDoc, t: number): TranscriptPlayhead {
  if (doc.lines.length === 0) return null

  const voEnd = voEndTime(doc)
  if (voEnd > 0 && t >= voEnd) {
    const scene = activeSceneAt(doc, t)
    return { kind: 'editorial', sceneId: scene?.id ?? null }
  }

  for (const kf of doc.lineKeyframes) {
    const line = doc.lines.find((l) => l.id === kf.parentId)
    if (!line) continue
    const kt = keyframeTime(kf.kind, line.words, kf.gapIndex)
    if (Math.abs(kt - t) <= KEYFRAME_EPS) {
      return { kind: 'keyframe', lineId: line.id, keyframeId: kf.id }
    }
  }

  for (const line of doc.lines) {
    for (const word of line.words) {
      if (t >= word.start && t < word.end) {
        return { kind: 'word', lineId: line.id, wordId: word.id, word }
      }
    }
  }

  for (const line of doc.lines) {
    for (let i = 0; i < line.words.length - 1; i++) {
      const left = line.words[i]!
      const right = line.words[i + 1]!
      if (t >= left.end && t < right.start) {
        return { kind: 'gap', lineId: line.id, gapIndex: i + 1 }
      }
    }
  }

  // Inter-line silence and pre-roll map to the next line's leading gap.
  for (let li = 0; li < doc.lines.length; li++) {
    const line = doc.lines[li]!
    const first = line.words[0]
    if (!first) continue
    if (t < first.start) {
      return { kind: 'gap', lineId: line.id, gapIndex: 0 }
    }
    const next = doc.lines[li + 1]
    const last = line.words[line.words.length - 1]
    if (next && last && t >= last.end) {
      const nextFirst = next.words[0]
      if (nextFirst && t < nextFirst.start) {
        return { kind: 'gap', lineId: next.id, gapIndex: 0 }
      }
    }
  }

  return null
}

export function transcriptEndTime(doc: ProjectDoc): number {
  let end = 0
  for (const line of doc.lines) {
    for (const word of line.words) {
      if (word.end > end) end = word.end
    }
  }
  return end
}

/** Full VO script from transcript lines. */
export function transcriptScript(doc: ProjectDoc): string {
  return doc.lines
    .map((line) => line.words.map((w) => w.text).join(' '))
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
}