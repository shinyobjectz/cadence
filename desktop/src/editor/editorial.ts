import type { ProjectDoc, SceneBlock } from './types'
import { transcriptEndTime } from './transcriptPlayhead'

/** First second where VO narration has finished (max word end). */
export function voEndTime(doc: ProjectDoc): number {
  return transcriptEndTime(doc)
}

/** Playhead is past synced VO — montage, holds, end cards live here. */
export function isEditorialTail(doc: ProjectDoc, t: number): boolean {
  const voEnd = voEndTime(doc)
  return voEnd > 0 && t >= voEnd
}

/** Scene brief block active at `t` (inclusive start, exclusive end). */
export function activeSceneAt(doc: ProjectDoc, t: number): SceneBlock | null {
  let hit: SceneBlock | null = null
  for (const scene of doc.scenes) {
    if (t >= scene.at && t < scene.at + scene.duration) hit = scene
  }
  return hit
}

/** Suggested montage / CTA starts from VO end — used when authoring editorial tail. */
export function defaultEditorialScenes(voEnd: number) {
  const montageAt = roundT(voEnd + 0.4)
  const montageDur = 3
  const ctaAt = roundT(montageAt + montageDur + 0.3)
  const ctaDur = 2.2
  const duration = roundT(ctaAt + ctaDur + 0.4)
  return { montageAt, montageDur, ctaAt, ctaDur, duration }
}

function roundT(t: number) {
  return Math.round(t * 100) / 100
}
