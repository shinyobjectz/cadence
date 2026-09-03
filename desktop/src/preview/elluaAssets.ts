import { resolveAssetUrl } from '../cadence/api'

const cache = new Map<string, string>()

export const ELLUA_ASSET_PATH_RE = /evals\/assets\/[A-Za-z0-9_./-]+/g
export const PROJECT_ASSET_PATH_RE = /assets\/(?:in|capture)\/[A-Za-z0-9_./-]+/g

export function collectAssetPaths(luaSrc: string): string[] {
  const evals = luaSrc.match(ELLUA_ASSET_PATH_RE) ?? []
  const project = luaSrc.match(PROJECT_ASSET_PATH_RE) ?? []
  return [...new Set([...evals, ...project])]
}

export type AssetResolver = (rel: string) => Promise<string>

export function createAssetResolver(projectPath: string | null): AssetResolver {
  return async (rel: string) => {
    const key = `${projectPath ?? ''}:${rel}`
    const hit = cache.get(key)
    if (hit) return hit
    const url = await resolveAssetUrl(projectPath, rel)
    cache.set(key, url)
    return url
  }
}

/** @deprecated use createAssetResolver */
export async function resolveElluaAsset(rel: string): Promise<string> {
  return createAssetResolver(null)(rel)
}
