/**
 * Tier-0 wasmoon compile — host-free preview parity without LÖVE.
 * Used by cadence verify / agent feedback loops.
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = path.resolve(fileURLToPath(new URL('../..', import.meta.url)))
const DESKTOP = path.join(ROOT, 'desktop')

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
]

function wasmPath() {
  return pathToFileURL(path.join(DESKTOP, 'node_modules/wasmoon/dist/glue.wasm')).href
}

async function loadLuaFactory() {
  const entry = path.join(DESKTOP, 'node_modules/wasmoon/dist/index.js')
  try {
    await fs.access(entry)
  } catch {
    throw new Error('wasmoon missing — run: cd desktop && pnpm install')
  }
  const mod = await import(pathToFileURL(entry).href)
  return mod.LuaFactory
}

async function readLib(name) {
  if (name === 'bridge.lua') {
    return fs.readFile(path.join(ROOT, 'web/bridge.lua'), 'utf8')
  }
  const cadence = path.join(ROOT, 'lib/cadence', name)
  try {
    return await fs.readFile(cadence, 'utf8')
  } catch {
    return fs.readFile(path.join(ROOT, 'lib/ellua', name), 'utf8')
  }
}

/** @returns {Promise<{ ok: true, width: number, height: number, duration: number, fps: number } | { ok: false, error: string }>} */
export async function wasmCompile(source) {
  const LuaFactory = await loadLuaFactory()
  const factory = new LuaFactory(wasmPath())
  for (const name of LIB) {
    const src = await readLib(name)
    await factory.mountFile(`cadence/${name}`, src)
    await factory.mountFile(`ellua/${name}`, src)
  }
  await factory.mountFile('bridge.lua', await readLib('bridge.lua'))
  const lua = await factory.createEngine()
  try {
    lua.doStringSync(`
      unpack = table.unpack
      math.atan2 = math.atan2 or math.atan
      package.path = "?.lua;?/init.lua;" .. package.path
      math.randomseed(0)
      require("bridge")
    `)
    lua.global.set('__measure_text', () => [100, 32])
    const raw = lua.global.call('compile_comp_safe', source)?.[0]
    if (raw?.ok === false) {
      return { ok: false, error: String(raw.error || 'compile failed') }
    }
    return {
      ok: true,
      width: Number(raw?.width) || 1280,
      height: Number(raw?.height) || 720,
      duration: Number(raw?.duration) || 3,
      fps: Number(raw?.fps) || 30,
    }
  } finally {
    lua.global.close()
  }
}

export { ROOT }
