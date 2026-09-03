import { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { LuaFactory, type LuaEngine } from 'wasmoon'
import wasmUrl from 'wasmoon/dist/glue.wasm?url'
import { paint, measureText } from '../../../web/painter.js'
import { formatError } from '../studioContext'

const LIB = [
  'init.lua',
  'timeline.lua',
  'color.lua',
  'ease.lua',
  'hsluv.lua',
  'json.lua',
  'okhsl.lua',
  'rough.lua',
  'chart.lua',
  'ornament.lua',
  'captions.lua',
  'spine.lua',
  'audio.lua',
] as const

const PREVIEW_WIDTH = 220

type Meta = {
  width: number
  height: number
  duration: number
  fps: number
}

type WasmoonFrameProps = {
  luaSource: string
  inputs?: Record<string, string>
  onError?: (message: string) => void
}

function callLua(lua: LuaEngine, name: string, ...args: unknown[]): unknown {
  const top = lua.global.getTop()
  try {
    const results = lua.global.call(name, ...args)
    return results && results.length ? results[0] : undefined
  } finally {
    const extra = lua.global.getTop() - top
    if (extra > 0) lua.global.pop(extra)
  }
}

function runLua(lua: LuaEngine, script: string): unknown {
  const top = lua.global.getTop()
  try {
    return lua.doStringSync(script)
  } finally {
    const extra = lua.global.getTop() - top
    if (extra > 0) lua.global.pop(extra)
  }
}

type CompileResult = Meta & { ok?: boolean; error?: string }

function asMeta(raw: unknown): Meta {
  const row = raw as CompileResult | null
  if (row?.ok === false) {
    throw new Error(row.error || 'compile failed')
  }
  return {
    width: Number(row?.width) || 1280,
    height: Number(row?.height) || 720,
    duration: Number(row?.duration) || 2,
    fps: Number(row?.fps) || 30,
  }
}

function snapshotPaint(
  lua: LuaEngine,
  canvas: HTMLCanvasElement | null,
  seconds: number,
  size: Meta,
) {
  if (!canvas) return
  const json = callLua(lua, 'snapshot', seconds)
  const frame = JSON.parse(String(json)) as unknown
  canvas.width = size.width
  canvas.height = size.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('2d context unavailable')
  paint(ctx, frame, seconds)
}

export function WasmoonFrame({ luaSource, inputs, onError }: WasmoonFrameProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const luaRef = useRef<LuaEngine | null>(null)
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError

  const [epoch, setEpoch] = useState(0)
  const [t, setT] = useState(0)
  const [meta, setMeta] = useState<Meta>({ width: 1280, height: 720, duration: 4, fps: 30 })
  const [status, setStatus] = useState('loading Lua WASM…')
  const inputsKey = inputs ? JSON.stringify(inputs) : ''

  useEffect(() => {
    let cancelled = false
    let engine: LuaEngine | null = null

    void (async () => {
      try {
        const factory = new LuaFactory(wasmUrl)
        for (const name of LIB) {
          const src = await invoke<string>('read_ellua_lib', { name })
          await factory.mountFile(`cadence/${name}`, src)
          await factory.mountFile(`ellua/${name}`, src)
        }
        await factory.mountFile(
          'bridge.lua',
          await invoke<string>('read_ellua_lib', { name: 'bridge.lua' }),
        )
        engine = await factory.createEngine()
        if (cancelled) {
          engine.global.close()
          return
        }
        runLua(
          engine,
          `
    unpack = table.unpack
    math.atan2 = math.atan2 or math.atan
    package.path = "?.lua;?/init.lua;" .. package.path
    math.randomseed(0)
    require("bridge")
  `,
        )
        engine.global.set('__measure_text', (text: unknown, size: unknown, font: unknown) =>
          measureText(
            String(text ?? ''),
            Number(size) || 32,
            font ? String(font) : null,
          ),
        )
        luaRef.current = engine
        setEpoch((n) => n + 1)
        setStatus('')
      } catch (err) {
        if (!cancelled) {
          setStatus(formatError(err))
          onErrorRef.current?.(formatError(err))
        }
      }
    })()

    return () => {
      cancelled = true
      luaRef.current = null
      engine?.global.close()
    }
  }, [])

  useEffect(() => {
    if (epoch === 0) return
    const lua = luaRef.current
    if (!lua) return
    try {
      const parsedInputs = inputsKey ? (JSON.parse(inputsKey) as Record<string, string>) : undefined
      const raw =
        parsedInputs && Object.keys(parsedInputs).length > 0
          ? callLua(lua, 'compile_comp_safe', luaSource, parsedInputs)
          : callLua(lua, 'compile_comp_safe', luaSource)
      const next = asMeta(raw)
      setMeta(next)
      setT(0)
      setStatus('')
      snapshotPaint(lua, canvasRef.current, 0, next)
    } catch (err) {
      setStatus(formatError(err))
      onErrorRef.current?.(formatError(err))
    }
  }, [epoch, luaSource, inputsKey])

  useEffect(() => {
    if (epoch === 0) return
    const lua = luaRef.current
    if (!lua) return
    try {
      snapshotPaint(lua, canvasRef.current, t, meta)
    } catch (err) {
      setStatus(formatError(err))
      onErrorRef.current?.(formatError(err))
    }
  }, [epoch, t, meta])

  const scale = Math.min(1, PREVIEW_WIDTH / meta.width)
  const cssW = Math.round(meta.width * scale)
  const cssH = Math.round(meta.height * scale)

  return (
    <div className="studio-node-preview nodrag">
      <canvas
        ref={canvasRef}
        className="nodrag nowheel"
        style={{ width: cssW, height: cssH }}
      />
      <div className="studio-node-seek">
        <input
          className="nodrag nowheel"
          type="range"
          min={0}
          max={meta.duration}
          step={0.001}
          value={t}
          aria-label="Seek seconds"
          onChange={(ev) => setT(Number(ev.target.value))}
        />
        <span className="studio-node-seek-time">{t.toFixed(2)}s</span>
      </div>
      {status ? <div className="studio-node-status">{status}</div> : null}
    </div>
  )
}
