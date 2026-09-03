import type { HandleKind } from './types'

const PIN_KINDS = new Set<HandleKind>([
  'text',
  'image',
  'video',
  'audio',
  'font',
  'brand',
  'file',
  'comp',
])

const FILE_SOURCES = new Set<HandleKind>([
  'text',
  'image',
  'video',
  'audio',
  'font',
  'brand',
])

/** True when an output handle of `from` may connect to an input handle of `to`. */
export function canConnect(from: HandleKind, to: HandleKind): boolean {
  if (from === to && PIN_KINDS.has(from)) return true
  if (to === 'file' && FILE_SOURCES.has(from)) return true
  if (from === 'audio' && to === 'render') return true
  if (from === 'comp' && (to === 'preview' || to === 'render' || to === 'check')) {
    return true
  }
  if (from === 'video' && to === 'check') return true
  return false
}

/** Pin kinds plus node-type vetoes: audio never enters Comp; Check only from Comp/Render. */
export function connectionAllowed(
  from: HandleKind,
  to: HandleKind,
  targetNodeType: string | undefined,
  sourceNodeType?: string,
): boolean {
  if (from === 'audio' && targetNodeType === 'comp') return false
  if (targetNodeType === 'check' && sourceNodeType !== 'comp' && sourceNodeType !== 'render') {
    return false
  }
  return canConnect(from, to)
}

export function resolveHandleKind(
  nodeType: string | undefined,
  data: unknown,
  handleId: string | null | undefined,
  dir: 'source' | 'target',
): HandleKind | null {
  if (nodeType === 'file' && dir === 'source') {
    const kind = (data as { kind?: HandleKind } | undefined)?.kind
    return kind ?? 'file'
  }
  if (nodeType === 'comp' && dir === 'source') return 'comp'
  if (nodeType === 'comp' && dir === 'target') {
    const inputs = (data as { inputs?: { name: string; kind: HandleKind }[] } | undefined)
      ?.inputs
    if (!handleId || !inputs) return null
    return inputs.find((input) => input.name === handleId)?.kind ?? null
  }
  if (nodeType === 'render' && dir === 'source') return 'video'
  if (nodeType === 'render' && dir === 'target') {
    if (handleId === 'comp') return 'comp'
    if (handleId === 'audio') return 'audio'
    return null
  }
  if (nodeType === 'text' && dir === 'source') return 'text'
  if (nodeType === 'image-gen' && dir === 'source') return 'image'
  if (nodeType === 'image-gen' && dir === 'target') return 'text'
  if (nodeType === 'video-gen' && dir === 'source') return 'video'
  if (nodeType === 'video-gen' && dir === 'target') return 'text'
  if (nodeType === 'capture' && dir === 'source') {
    const outputs = (data as { outputs?: { id: string; kind: HandleKind }[] } | undefined)
      ?.outputs
    if (!handleId || !outputs) return null
    return outputs.find((output) => output.id === handleId)?.kind ?? null
  }
  if (nodeType === 'tts' && dir === 'source') return 'audio'
  if (nodeType === 'tts' && dir === 'target') return 'text'
  if (nodeType === 'check' && dir === 'target') {
    if (handleId === 'in') return 'check'
    if (handleId === 'comp') return 'comp'
    if (handleId === 'video') return 'video'
    return null
  }
  if (handleId && PIN_KINDS.has(handleId as HandleKind)) return handleId as HandleKind
  return null
}
