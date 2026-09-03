import type { SceneBlock, SceneParam, SceneSegment } from './types'

export type SceneToken = { kind: 'word'; text: string } | { kind: 'param'; paramId: string; text: string }

/** Flatten scene segments into word-level tokens for keyframe gaps. */
export function tokenizeScene(
  block: SceneBlock,
  params: Record<string, SceneParam>,
): SceneToken[] {
  const tokens: SceneToken[] = []
  for (const seg of block.segments) {
    if (seg.type === 'param') {
      const p = params[seg.paramId]
      tokens.push({
        kind: 'param',
        paramId: seg.paramId,
        text: p?.value ?? seg.paramId,
      })
      continue
    }
    const parts = seg.content.split(/(\s+)/)
    for (const part of parts) {
      if (!part || /^\s+$/.test(part)) continue
      tokens.push({ kind: 'word', text: part })
    }
  }
  return tokens
}

export function gapsForTokens(tokens: SceneToken[]) {
  return tokens.length + 1
}

/** Synthetic word timings within a scene block for keyframe resolution. */
export function sceneTokenTimings(block: SceneBlock, tokenCount: number) {
  const span = block.duration
  return Array.from({ length: tokenCount }, (_, i) => ({
    start: block.at + (i / tokenCount) * span,
    end: block.at + ((i + 1) / tokenCount) * span,
  }))
}

export function sceneTextPreview(segments: SceneSegment[], params: Record<string, SceneParam>) {
  return segments
    .map((s) => (s.type === 'text' ? s.content : params[s.paramId]?.value ?? '…'))
    .join('')
}
