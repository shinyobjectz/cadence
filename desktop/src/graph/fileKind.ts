import type { FileHandleKind } from './types'

const IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'tif', 'tiff', 'avif'])
const VIDEO = new Set(['mp4', 'webm', 'mov', 'mkv', 'avi', 'm4v'])
const AUDIO = new Set(['mp3', 'wav', 'aac', 'ogg', 'flac', 'm4a', 'opus'])

export function kindFromPath(path: string): FileHandleKind {
  const base = path.split(/[\\/]/).pop() ?? path
  const dot = base.lastIndexOf('.')
  const ext = dot >= 0 ? base.slice(dot + 1).toLowerCase() : ''
  if (IMAGE.has(ext)) return 'image'
  if (VIDEO.has(ext)) return 'video'
  if (AUDIO.has(ext)) return 'audio'
  return 'file'
}
