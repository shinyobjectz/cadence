import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import type { ProjectDoc } from '../../editor/types'
import { parseDoc, serializeDoc } from './doc'

export type CadenceProject = {
  path: string
  name: string
}

function basename(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts[parts.length - 1] ?? 'Untitled'
}

export async function pickProjectFolder(): Promise<string | null> {
  return invoke<string | null>('pick_folder')
}

/** Ensure project dirs exist (studio.json scaffold) and load or seed doc.json. */
export async function loadDoc(projectPath: string): Promise<ProjectDoc> {
  await invoke('open_project', { path: projectPath })
  await invoke('ensure_editor_vo_cmd', { project: projectPath, force: false })
  const raw = await invoke<string>('open_editor_doc', { project: projectPath })
  return parseDoc(raw)
}

/** Re-resolve VO when transcript text changed in-session. */
export async function ensureProjectVo(
  projectPath: string,
  force = false,
): Promise<{ regenerated: boolean; voPath?: string }> {
  const result = await invoke<{
    regenerated: boolean
    vo_path?: string | null
  }>('ensure_editor_vo_cmd', { project: projectPath, force })
  return {
    regenerated: result.regenerated,
    voPath: result.vo_path ?? undefined,
  }
}

export async function saveDoc(projectPath: string, doc: ProjectDoc): Promise<void> {
  await invoke('save_editor_doc', {
    project: projectPath,
    json: serializeDoc(doc),
  })
}

export async function readCompSource(
  projectPath: string | null,
  compPath: string,
): Promise<string> {
  if (compPath.startsWith('evals/')) {
    return invoke<string>('read_ellua_eval', { rel: compPath })
  }
  if (!projectPath) {
    throw new Error(`project required to load ${compPath}`)
  }
  return invoke<string>('read_project_file', { project: projectPath, rel: compPath })
}

export async function readCompInputs(
  projectPath: string,
  compPath: string,
): Promise<Record<string, string>> {
  if (!compPath.startsWith('comps/') || !compPath.endsWith('.lua')) {
    return {}
  }
  const jsonRel = `${compPath.slice(0, -4)}.inputs.json`
  try {
    const raw = await invoke<string>('read_project_file', {
      project: projectPath,
      rel: jsonRel,
    })
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const out: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'string') out[key] = value
    }
    return out
  } catch {
    return {}
  }
}

export async function resolveAssetUrl(
  projectPath: string | null,
  rel: string,
): Promise<string> {
  const abs = rel.startsWith('evals/assets/')
    ? await invoke<string>('resolve_ellua_asset', { rel })
    : await invoke<string>('resolve_project_asset', { project: projectPath!, rel })
  return convertFileSrc(abs)
}

export async function defaultDemoProjectPath(): Promise<string> {
  return invoke<string>('resolve_cadence_path', { rel: 'evals/projects/launch-spot' })
}

export async function openProjectFromPicker(): Promise<CadenceProject | null> {
  const path = await pickProjectFolder()
  if (!path) return null
  return { path, name: basename(path) }
}
