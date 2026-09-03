import type { ParamType } from './types'

export type ParamValidation = { ok: true; value: string } | { ok: false; reason: string }

const INT_RE = /^-?\d+$/
const FLOAT_RE = /^-?\d*\.?\d+$/

export function validateParam(type: ParamType, raw: string): ParamValidation {
  const value = raw.trim()
  if (value === '') return { ok: false, reason: 'Value required' }

  switch (type) {
    case 'integer':
      if (!INT_RE.test(value)) return { ok: false, reason: 'Integer only' }
      return { ok: true, value }
    case 'float':
      if (!FLOAT_RE.test(value) || value === '.' || value === '-.')
        return { ok: false, reason: 'Number only' }
      return { ok: true, value }
    case 'string':
      return { ok: true, value }
    case 'list': {
      try {
        const parsed = JSON.parse(value)
        if (!Array.isArray(parsed)) return { ok: false, reason: 'Must be a JSON array' }
        return { ok: true, value }
      } catch {
        return { ok: false, reason: 'Invalid JSON array' }
      }
    }
    case 'map': {
      try {
        const parsed = JSON.parse(value)
        if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed))
          return { ok: false, reason: 'Must be a JSON object' }
        return { ok: true, value }
      } catch {
        return { ok: false, reason: 'Invalid JSON object' }
      }
    }
  }
}

/** Restrict keystrokes while typing — rejects invalid chars before they appear. */
export function filterParamInput(type: ParamType, next: string, _prev: string): string {
  switch (type) {
    case 'integer':
      return next.replace(/[^\d-]/g, '').replace(/(?!^)-/g, '')
    case 'float': {
      let s = next.replace(/[^\d.-]/g, '').replace(/(?!^)-/g, '')
      const parts = s.split('.')
      if (parts.length > 2) s = `${parts[0]}.${parts.slice(1).join('')}`
      return s
    }
    case 'string':
      return next
    case 'list':
    case 'map':
      return next
    default:
      return next
  }
}

export function paramTypeLabel(type: ParamType): string {
  switch (type) {
    case 'integer':
      return 'int'
    case 'float':
      return 'float'
    case 'string':
      return 'str'
    case 'list':
      return 'list'
    case 'map':
      return 'map'
  }
}

export function paramTypeClass(type: ParamType): string {
  switch (type) {
    case 'integer':
      return 'bg-amber-500/15 text-amber-800 dark:text-amber-200 ring-amber-500/30'
    case 'float':
      return 'bg-sky-500/15 text-sky-800 dark:text-sky-200 ring-sky-500/30'
    case 'string':
      return 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-200 ring-emerald-500/30'
    case 'list':
      return 'bg-violet-500/15 text-violet-800 dark:text-violet-200 ring-violet-500/30'
    case 'map':
      return 'bg-orange-500/15 text-orange-800 dark:text-orange-200 ring-orange-500/30'
  }
}
