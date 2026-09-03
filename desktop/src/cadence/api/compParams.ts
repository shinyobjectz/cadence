import type { SceneParam } from '../../editor/types'
import { toLuaValue } from './luaValue'

/** @deprecated use buildDocLua / injectDocBinding */
export function buildDocParamsLua(sceneParams: Record<string, SceneParam>): string {
  const out: Record<string, unknown> = {}
  for (const param of Object.values(sceneParams)) {
    switch (param.type) {
      case 'integer':
      case 'float':
        out[param.name] = Number(param.value)
        break
      case 'string':
        out[param.name] = param.value
        break
      case 'list':
      case 'map':
        try {
          out[param.name] = JSON.parse(param.value)
        } catch {
          out[param.name] = param.value
        }
        break
    }
  }
  return `__doc_params = ${toLuaValue(out)}\n`
}

/** @deprecated use injectDocBinding */
export function injectDocParams(luaSource: string, sceneParams: Record<string, SceneParam>): string {
  return buildDocParamsLua(sceneParams) + luaSource
}

export { injectDocBinding, buildDocLua, buildDocBinding, docBindingKey } from './docBinding'
