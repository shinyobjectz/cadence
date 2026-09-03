import { useEffect, useRef, useState } from 'react'
import { Tip } from '../components/ui/tooltip'
import { cn } from '../lib/utils'
import {
  filterParamInput,
  paramTypeClass,
  paramTypeLabel,
  validateParam,
} from './params'
import { useEditor } from './store'
import type { SceneParam } from './types'

export function ParamChip({ param }: { param: SceneParam }) {
  const updateSceneParam = useEditor((s) => s.updateSceneParam)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState(param.value)
  const editorRef = useRef<HTMLSpanElement>(null)
  const prefix = param.prefix ?? ''
  const suffix = param.suffix ?? ''
  const multiline = param.type === 'list' || param.type === 'map'

  useEffect(() => {
    setDraft(param.value)
    setError(null)
    if (editorRef.current) editorRef.current.textContent = param.value
  }, [param.value])

  useEffect(() => {
    if (editorRef.current && !editorRef.current.textContent) {
      editorRef.current.textContent = param.value
    }
  }, [])

  const commit = (raw: string) => {
    const filtered =
      param.type === 'integer' || param.type === 'float'
        ? filterParamInput(param.type, raw, draft)
        : raw
    setDraft(filtered)
    if (editorRef.current && editorRef.current.textContent !== filtered) {
      editorRef.current.textContent = filtered
    }
    const result = validateParam(param.type, filtered)
    if (!result.ok) {
      setError(result.reason)
      return
    }
    setError(null)
    updateSceneParam(param.id, result.value)
  }

  const onInput = () => {
    commit(editorRef.current?.textContent ?? '')
  }

  const onBlur = () => {
    const result = validateParam(param.type, draft)
    if (!result.ok) {
      setDraft(param.value)
      if (editorRef.current) editorRef.current.textContent = param.value
      setError(null)
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !multiline) e.preventDefault()
  }

  const onPaste = (e: React.ClipboardEvent) => {
    if (multiline) return
    e.preventDefault()
    const text = e.clipboardData.getData('text/plain').replace(/\s+/g, '')
    commit(text)
  }

  const tip = [param.name, paramTypeLabel(param.type), suffix && `suffix: ${suffix}`]
    .filter(Boolean)
    .join(' · ')

  return (
    <Tip label={tip}>
      <span
        className={cn(
          'inline max-w-[min(100%,22rem)] align-baseline',
          'rounded px-1.5 py-0.5 font-mono text-[12px] leading-normal',
          'whitespace-pre-wrap break-words [word-spacing:normal]',
          'ring-1 ring-inset',
          paramTypeClass(param.type),
          error && 'ring-destructive/60',
        )}
      >
        {prefix ? (
          <span aria-hidden className="select-none text-muted-foreground/70">
            {prefix}
          </span>
        ) : null}
        <span
          ref={editorRef}
          role="textbox"
          contentEditable
          suppressContentEditableWarning
          tabIndex={0}
          onInput={onInput}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          className={cn(
            'inline min-w-[1ch] outline-none',
            param.type === 'integer' || param.type === 'float' ? 'tabular-nums' : '',
          )}
          aria-label={param.name}
          aria-invalid={!!error}
        />
        {suffix ? (
          <span aria-hidden className="select-none text-muted-foreground/70">
            {suffix}
          </span>
        ) : null}
      </span>
    </Tip>
  )
}
