/** Serialize a JS value to a Lua literal for injection into comp prelude. */
export function toLuaValue(v: unknown): string {
  if (v === null || v === undefined) return 'nil'
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'nil'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'string') return JSON.stringify(v)
  if (Array.isArray(v)) {
    const parts = v.map((item, i) => `[${i + 1}] = ${toLuaValue(item)}`)
    return `{ ${parts.join(', ')} }`
  }
  if (typeof v === 'object') {
    const parts = Object.entries(v as Record<string, unknown>).map(
      ([k, val]) => `[${JSON.stringify(k)}] = ${toLuaValue(val)}`,
    )
    return `{ ${parts.join(', ')} }`
  }
  return 'nil'
}
