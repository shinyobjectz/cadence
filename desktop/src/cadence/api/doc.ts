import type { ProjectDoc } from '../../editor/types'

/** On-disk doc.json envelope (version 1). */
export type DocFile = ProjectDoc & { version: 1 }

export function parseDoc(raw: string): ProjectDoc {
  const data = JSON.parse(raw) as Partial<DocFile>
  if (data.version !== 1) {
    throw new Error(`unsupported doc.json version: ${String(data.version)}`)
  }
  if (typeof data.duration !== 'number' || !Number.isFinite(data.duration)) {
    throw new Error('doc.json: duration must be a finite number')
  }
  if (typeof data.fps !== 'number' || data.fps <= 0) {
    throw new Error('doc.json: fps must be a positive number')
  }
  if (data.aspect !== '16:9' && data.aspect !== '9:16') {
    throw new Error('doc.json: aspect must be 16:9 or 9:16')
  }
  if (typeof data.compPath !== 'string' || !data.compPath) {
    throw new Error('doc.json: compPath is required')
  }
  return {
    duration: data.duration,
    fps: data.fps,
    aspect: data.aspect,
    compPath: data.compPath,
    voPath: typeof data.voPath === 'string' && data.voPath ? data.voPath : undefined,
    voKey: typeof data.voKey === 'string' && data.voKey ? data.voKey : undefined,
    voAlignKey:
      typeof data.voAlignKey === 'string' && data.voAlignKey ? data.voAlignKey : undefined,
    voProvider:
      typeof data.voProvider === 'string' && data.voProvider ? data.voProvider : undefined,
    lines: Array.isArray(data.lines) ? data.lines : [],
    lineKeyframes: Array.isArray(data.lineKeyframes) ? data.lineKeyframes : [],
    scenes: Array.isArray(data.scenes) ? data.scenes : [],
    sceneParams:
      data.sceneParams && typeof data.sceneParams === 'object' ? data.sceneParams : {},
  }
}

export function serializeDoc(doc: ProjectDoc): string {
  const file: DocFile = { version: 1, ...doc }
  return JSON.stringify(file, null, 2)
}
