import { useCallback, useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { LuaFactory, type LuaEngine } from 'wasmoon'
import wasmUrl from 'wasmoon/dist/glue.wasm?url'
import { paint, measureText } from '../../../web/painter.js'
import { MediaBag, assetUrl } from '../../../web/media.js'
import { readCompSource, injectDocBinding, docBindingKey } from '../cadence/api'
import type { ProjectDoc } from '../editor/types'
import { formatError } from '../studioContext'
import { formatFindingsStatus, parseCadenceJson } from '../cadence/findings'
import { createAssetResolver } from './elluaAssets'
import { loadMediaFromComp } from './loadMediaBag'

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

export type CompMeta = {
  width: number
  height: number
  duration: number
  fps: number
}

function callLua(lua: LuaEngine, name: string, ...args: unknown[]): unknown {
  const top = lua.global.getTop()
  try {
    const results = lua.global.call(name, ...args)
    return results?.length ? results[0] : undefined
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

function asMeta(raw: unknown): CompMeta {
  const row = raw as { width?: number; height?: number; duration?: number; fps?: number } | null
  return {
    width: Number(row?.width) || 1280,
    height: Number(row?.height) || 720,
    duration: Number(row?.duration) || 3,
    fps: Number(row?.fps) || 30,
  }
}

type CompileResult = CompMeta & { ok?: boolean; error?: string }

function parseCompileResult(raw: unknown): CompileResult {
  const row = raw as CompileResult | null
  if (row?.ok === false) {
    throw new Error(row.error || 'compile failed')
  }
  return asMeta(raw)
}

/** Hot-reload a cadence lib module (wasmoon caches require() from first boot). */
async function reloadCadenceLib(lua: LuaEngine, name: string) {
  const base = name.replace(/\.lua$/, '')
  const src = await invoke<string>('read_ellua_lib', { name })
  const top = lua.global.getTop()
  try {
    lua.global.set('__cadence_reload_src', src)
    runLua(
      lua,
      `
    local base = ${JSON.stringify(base)}
    package.loaded["cadence." .. base] = nil
    package.loaded["ellua." .. base] = nil
    local chunk, err = load(__cadence_reload_src, "@cadence/" .. base .. ".lua")
    if not chunk then error(err, 0) end
    package.loaded["cadence." .. base] = chunk()
    if base == "captions" then
      package.loaded["ellua.captions"] = package.loaded["cadence.captions"]
    end
  `,
    )
  } finally {
    lua.global.set('__cadence_reload_src', null)
    const extra = lua.global.getTop() - top
    if (extra > 0) lua.global.pop(extra)
  }
}

/** wasmoon + MediaBag preview engine for the editor viewport. */
export function useElluaPreview(compPath: string, projectPath: string | null, doc: ProjectDoc) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const luaRef = useRef<LuaEngine | null>(null)
  const mediaRef = useRef<MediaBag | null>(null)
  const metaRef = useRef<CompMeta>({ width: 1280, height: 720, duration: 3, fps: 30 })
  const skipCompAudioRef = useRef(false)

  skipCompAudioRef.current = Boolean(doc.voPath)

  const [booted, setBooted] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [meta, setMeta] = useState<CompMeta>(metaRef.current)
  const [status, setStatus] = useState('loading preview…')

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
          measureText(String(text ?? ''), Number(size) || 32, font ? String(font) : null),
        )
        luaRef.current = engine
        setBooted(true)
        setStatus('')
      } catch (err) {
        if (!cancelled) setStatus(formatError(err))
      }
    })()

    return () => {
      cancelled = true
      mediaRef.current?.dispose()
      mediaRef.current = null
      luaRef.current = null
      engine?.global.close()
      setBooted(false)
      setLoaded(false)
    }
  }, [])

  const bindingKey = docBindingKey(doc)

  useEffect(() => {
    if (!booted) return
    const lua = luaRef.current
    if (!lua) return
    let cancelled = false

    void (async () => {
      try {
        setLoaded(false)
        setStatus(`loading ${compPath}…`)
        const luaSource = await readCompSource(projectPath, compPath)
        const boundSource = injectDocBinding(luaSource, doc)
        mediaRef.current?.dispose()
        const media = new MediaBag()
        const resolveAsset = createAssetResolver(projectPath)
        const urlCache = new Map<string, string>()
        media.resolveUrl = (src: string) => urlCache.get(src) ?? assetUrl(src)
        await loadMediaFromComp(media, boundSource, async (src) => {
          const url = await resolveAsset(src)
          urlCache.set(src, url)
          return url
        })
        await reloadCadenceLib(lua, 'captions.lua')
        const next = parseCompileResult(callLua(lua, 'compile_comp_safe', boundSource))
        metaRef.current = next
        setMeta(next)
        const preview = JSON.parse(String(callLua(lua, 'snapshot', 0)))
        await media.warm(preview)
        mediaRef.current = media
        if (cancelled) return
        let lintHint = ''
        if (projectPath) {
          try {
            const raw = await invoke<string>('verify_comp', {
              project: projectPath,
              luaRel: compPath,
              skipCheck: true,
              skipLint: false,
              wasmOnly: false,
            })
            const verified = parseCadenceJson(raw)
            lintHint = formatFindingsStatus(verified)
          } catch {
            /* verify optional when node/love unavailable */
          }
        }
        setStatus(lintHint)
        setLoaded(true)
      } catch (err) {
        if (!cancelled) {
          setStatus(formatError(err))
          setLoaded(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [booted, compPath, projectPath, bindingKey])

  const drawFrame = useCallback((t: number, playing: boolean, volume = 1) => {
    const lua = luaRef.current
    const media = mediaRef.current
    const canvas = canvasRef.current
    const size = metaRef.current
    if (!lua || !media || !canvas) return

    media.playing = playing
    media.masterVolume = volume
    const json = callLua(lua, 'snapshot', t)
    const frame = JSON.parse(String(json)) as { audios?: unknown[]; nodes?: unknown[] }
    if (!skipCompAudioRef.current) {
      media.syncAudio(frame.audios, t, playing)
    }
    canvas.width = size.width
    canvas.height = size.height
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    paint(ctx, frame, t, media)
  }, [])

  const pauseMedia = useCallback(() => {
    mediaRef.current?.pauseAudio()
    mediaRef.current?.pauseVideos()
  }, [])

  return {
    canvasRef,
    meta,
    status,
    ready: booted && loaded,
    drawFrame,
    pauseMedia,
  }
}
