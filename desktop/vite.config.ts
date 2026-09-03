import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const desktopRoot = path.dirname(fileURLToPath(import.meta.url))
const elluaRoot = path.resolve(desktopRoot, '..')

/** Strip painter/media `?v=N` cache-busters so Vite can resolve the real files. */
function stripVersionQueryImports(): Plugin {
  return {
    name: 'strip-version-query-imports',
    enforce: 'pre',
    resolveId(source, importer) {
      const match = source.match(/^(.+)\?v=\d+$/)
      if (!match) return null
      return this.resolve(match[1], importer, { skipSelf: true })
    },
  }
}

/**
 * media.js uses `new URL('../' + src, import.meta.url)` for eval assets.
 * Vite treats that as a glob of ellua/ and would copy DESIGN.md, Cargo.lock, etc.
 * hello.lua needs cssFont only; keep the real media.js, drop the glob.
 */
function neutralizeMediaAssetGlob(): Plugin {
  return {
    name: 'neutralize-media-asset-glob',
    enforce: 'pre',
    transform(code, id) {
      const normalized = id.replaceAll('\\', '/')
      if (!normalized.endsWith('/web/media.js')) return null
      return {
        code: code.replace(
          'return new URL(`../${src}`, import.meta.url).href;',
          'return src;',
        ),
        map: null,
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [tailwindcss(), stripVersionQueryImports(), neutralizeMediaAssetGlob(), react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    fs: {
      allow: [elluaRoot],
    },
    watch: {
      ignored: ['**/src-tauri/**'],
    },
  },
})
