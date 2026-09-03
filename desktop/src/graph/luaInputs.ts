import type { CompInput, HandleKind } from './types'

export type LuaInput = CompInput

const PIN_KINDS = new Set<string>([
  'text',
  'image',
  'video',
  'audio',
  'font',
  'brand',
  'file',
  'comp',
])

function findInputsTable(source: string): string | null {
  const match = source.match(/\binputs\s*=\s*\{/)
  if (!match || match.index === undefined) return null
  const start = match.index + match[0].length - 1
  let depth = 0
  for (let i = start; i < source.length; i++) {
    const ch = source[i]
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) return source.slice(start + 1, i)
    }
  }
  return null
}

export function scanLuaInputs(source: string): LuaInput[] {
  const body = findInputsTable(source)
  if (!body) return []

  const inputs: LuaInput[] = []
  const entry = /\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{([^}]*)\}/g
  for (const match of body.matchAll(entry)) {
    const name = match[1]
    const spec = match[2]
    if (!name || spec === undefined) continue
    const kindMatch = spec.match(/\bkind\s*=\s*"([^"]+)"/)
    const kind = kindMatch?.[1]
    if (!kind || !PIN_KINDS.has(kind)) continue
    inputs.push({ name, kind: kind as HandleKind })
  }
  return inputs
}
