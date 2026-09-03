export type HandleKind =
  | 'text'
  | 'image'
  | 'video'
  | 'audio'
  | 'font'
  | 'brand'
  | 'file'
  | 'comp'
  | 'render'
  | 'preview'
  | 'check'

export type GraphNode = {
  id: string
  type?: string
  data?: Record<string, unknown>
}

export type GraphEdge = {
  source: string
  target: string
}

export type HashMap = Readonly<Record<string, string>>

export type FileHandleKind = Extract<HandleKind, 'image' | 'video' | 'audio' | 'file'>

export type FileNodeData = {
  path?: string
  kind: FileHandleKind
}

export type CompInput = {
  name: string
  kind: HandleKind
}

export type CompNodeData = {
  luaPath?: string
  inputs: CompInput[]
  /** Bumped after write_comp_inputs so Wasmoon re-reads inputs.json. */
  inputsTick?: number
}

export type RenderQuality = 'draft' | 'standard' | 'high'

export type RenderNodeData = {
  quality?: RenderQuality
  outputRel?: string
  stderr?: string
  jobId?: string
  status?: 'idle' | 'running' | 'succeeded' | 'failed' | 'cancelled'
}

export type CheckMode = 'lint' | 'check' | 'hash'

export type CheckNodeData = {
  mode?: CheckMode
  jobId?: string
  status?: 'idle' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  output?: string
  findings?: import('../cadence/findings').CadenceFinding[]
  result?: import('../cadence/findings').CadenceResult | null
}

export type GenStatus = 'idle' | 'running' | 'succeeded' | 'failed'

export type TextNodeData = {
  prompt?: string
  model?: string
  text?: string
  jobId?: string
  status?: GenStatus
  error?: string
}

export type ImageGenNodeData = {
  prompt?: string
  model?: string
  outputRel?: string
  status?: GenStatus
  error?: string
}

export type VideoGenNodeData = {
  prompt?: string
  model?: string
  outputRel?: string
  status?: GenStatus
  error?: string
}

export type JobRunStatus = 'idle' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type CaptureAsset = {
  id: string
  kind: HandleKind
  rel: string
}

export type CaptureNodeData = {
  url?: string
  host?: string
  outputs?: CaptureAsset[]
  jobId?: string
  status?: JobRunStatus
  error?: string
}

export type TtsKind = 'tts' | 'sfx' | 'music'

export type TtsNodeData = {
  kind?: TtsKind
  text?: string
  outputRel?: string
  jobId?: string
  status?: JobRunStatus
  error?: string
}
