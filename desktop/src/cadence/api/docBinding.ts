import { voEndTime } from '../../editor/editorial'
import { keyframeTime } from '../../editor/time'
import type { ProjectDoc, SceneParam } from '../../editor/types'
import { toLuaValue } from './luaValue'

export type ResolvedKeyframe = {
  id: string
  kind: string
  t: number
  lineId: string
  gapIndex: number
}

export type DocBinding = {
  version: 1
  duration: number
  voEnd: number
  fps: number
  aspect: string
  voPath?: string
  params: Record<string, unknown>
  scenes: { id: string; at: number; duration: number }[]
  keyframes: ResolvedKeyframe[]
  words: { text: string; t0: number; t1: number }[]
  lines: { id: string; words: { text: string; t0: number; t1: number }[] }[]
}

function parseParamValue(param: SceneParam): unknown {
  switch (param.type) {
    case 'integer':
    case 'float':
      return Number(param.value)
    case 'string':
      return param.value
    case 'list':
    case 'map':
      try {
        return JSON.parse(param.value)
      } catch {
        return param.value
      }
  }
}

export function buildDocBinding(doc: ProjectDoc): DocBinding {
  const params: Record<string, unknown> = {}
  for (const param of Object.values(doc.sceneParams)) {
    params[param.name] = parseParamValue(param)
  }

  const keyframes: ResolvedKeyframe[] = doc.lineKeyframes.map((kf) => {
    const line = doc.lines.find((l) => l.id === kf.parentId)
    const words = line?.words ?? []
    return {
      id: kf.id,
      kind: kf.kind,
      t: keyframeTime(kf.kind, words, kf.gapIndex),
      lineId: kf.parentId,
      gapIndex: kf.gapIndex,
    }
  })

  const words = doc.lines.flatMap((line) =>
    line.words.map((w) => ({ text: w.text, t0: w.start, t1: w.end })),
  )

  const lines = doc.lines.map((line) => ({
    id: line.id,
    words: line.words.map((w) => ({ text: w.text, t0: w.start, t1: w.end })),
  }))

  return {
    version: 1,
    duration: doc.duration,
    voEnd: voEndTime(doc),
    fps: doc.fps,
    aspect: doc.aspect,
    voPath: doc.voPath,
    params,
    scenes: doc.scenes.map((s) => ({ id: s.id, at: s.at, duration: s.duration })),
    keyframes,
    words,
    lines,
  }
}

export function buildDocLua(doc: ProjectDoc): string {
  const binding = buildDocBinding(doc)
  const params = binding.params
  return [
    `__doc = ${toLuaValue(binding)}`,
    `-- legacy alias for comps that only read params`,
    `__doc_params = ${toLuaValue(params)}`,
    '',
  ].join('\n')
}

export function injectDocBinding(luaSource: string, doc: ProjectDoc): string {
  return buildDocLua(doc) + luaSource
}

/** Stable key for preview recompile when editorial data changes. */
export function docBindingKey(doc: ProjectDoc): string {
  return JSON.stringify(buildDocBinding(doc))
}
