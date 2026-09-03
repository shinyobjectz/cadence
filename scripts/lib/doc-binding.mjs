/** Minimal doc.json → __doc prelude for agent verify (mirrors desktop/docBinding.ts). */
import fs from 'node:fs/promises'
import path from 'node:path'

function toLuaValue(v) {
  if (v === null || v === undefined) return 'nil'
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'nil'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'string') return JSON.stringify(v)
  if (Array.isArray(v)) {
    const parts = v.map((item, i) => `[${i + 1}] = ${toLuaValue(item)}`)
    return `{ ${parts.join(', ')} }`
  }
  if (typeof v === 'object') {
    const parts = Object.entries(v).map(([k, val]) => `[${JSON.stringify(k)}] = ${toLuaValue(val)}`)
    return `{ ${parts.join(', ')} }`
  }
  return 'nil'
}

function transcriptEndTime(doc) {
  let end = 0
  for (const line of doc.lines || []) {
    for (const word of line.words || []) {
      if (word.end > end) end = word.end
    }
  }
  return end
}

function keyframeTime(kind, words, gapIndex) {
  if (!words?.length) return 0
  if (gapIndex <= 0) return words[0].start
  if (gapIndex >= words.length) return words[words.length - 1].end
  const left = words[gapIndex - 1]
  const right = words[gapIndex]
  if (kind === 'open') return right.start
  if (kind === 'close') return left.end
  return (left.end + right.start) / 2
}

function parseParamValue(param) {
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
    default:
      return param.value
  }
}

function buildDocBinding(doc) {
  const params = {}
  for (const param of Object.values(doc.sceneParams || {})) {
    params[param.name] = parseParamValue(param)
  }
  const keyframes = (doc.lineKeyframes || []).map((kf) => {
    const line = (doc.lines || []).find((l) => l.id === kf.parentId)
    const words = line?.words ?? []
    return {
      id: kf.id,
      kind: kf.kind,
      t: keyframeTime(kf.kind, words, kf.gapIndex),
      lineId: kf.parentId,
      gapIndex: kf.gapIndex,
    }
  })
  const words = (doc.lines || []).flatMap((line) =>
    (line.words || []).map((w) => ({ text: w.text, t0: w.start, t1: w.end })),
  )
  const lines = (doc.lines || []).map((line) => ({
    id: line.id,
    words: (line.words || []).map((w) => ({ text: w.text, t0: w.start, t1: w.end })),
  }))
  return {
    version: 1,
    duration: doc.duration,
    voEnd: transcriptEndTime(doc),
    fps: doc.fps,
    aspect: doc.aspect,
    voPath: doc.voPath,
    params,
    scenes: (doc.scenes || []).map((s) => ({ id: s.id, at: s.at, duration: s.duration })),
    keyframes,
    words,
    lines,
  }
}

export function injectDocBinding(luaSource, doc) {
  const binding = buildDocBinding(doc)
  return [
    `__doc = ${toLuaValue(binding)}`,
    `__doc_params = ${toLuaValue(binding.params)}`,
    '',
    luaSource,
  ].join('\n')
}

export async function loadBoundCompSource(projectDir, compRel, rawSource) {
  const docPath = path.join(projectDir, 'doc.json')
  try {
    const doc = JSON.parse(await fs.readFile(docPath, 'utf8'))
    return injectDocBinding(rawSource, doc)
  } catch {
    return rawSource
  }
}
