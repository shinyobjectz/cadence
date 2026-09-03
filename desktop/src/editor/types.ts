/** Keyframe anchored between words on a transcript line. */
export type KeyframeKind = 'open' | 'mid' | 'close'

export type Keyframe = {
  id: string
  kind: KeyframeKind
  /** Gap index: 0 = before first word, n = after word n-1 / before word n */
  gapIndex: number
  parentId: string
  parentType: 'line'
}

export type Word = {
  id: string
  text: string
  start: number
  end: number
}

export type TranscriptLine = {
  id: string
  words: Word[]
}

export type ParamType = 'integer' | 'float' | 'string' | 'list' | 'map'

/** SDK-linked editable value surfaced inline in scene descriptions. */
export type SceneParam = {
  id: string
  name: string
  type: ParamType
  value: string
  prefix?: string
  suffix?: string
}

export type SceneSegment =
  | { type: 'text'; content: string }
  | { type: 'param'; paramId: string }

/** Read-only scene description block with inline param spans. */
export type SceneBlock = {
  id: string
  at: number
  duration: number
  segments: SceneSegment[]
}

export type EditorTool = 'select' | 'lasso' | 'comment' | 'text'

export type ProjectDoc = {
  duration: number
  fps: number
  aspect: '16:9' | '9:16'
  /** Cadence comp evaluated by the preview player */
  compPath: string
  /** Project-relative path to generated VO audio (assets/in/…) */
  voPath?: string
  /** Content hash — transcript + provider; stale triggers resolve */
  voKey?: string
  /** Set when word timings were aligned to voPath */
  voAlignKey?: string
  voProvider?: string
  lines: TranscriptLine[]
  lineKeyframes: Keyframe[]
  scenes: SceneBlock[]
  sceneParams: Record<string, SceneParam>
}
