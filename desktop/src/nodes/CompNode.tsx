import { useContext, useEffect, useState } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { invoke } from '@tauri-apps/api/core'
import { scanLuaInputs } from '../graph/luaInputs'
import type { CompNodeData } from '../graph/types'
import { isNativeOnly, shouldPreview } from '../preview/nativeOnly'
import { WasmoonFrame } from '../preview/WasmoonFrame'
import { StudioContext, formatError } from '../studioContext'

function parseInputsJson(text: string): Record<string, string> | undefined {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return undefined
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined
  const map: Record<string, string> = {}
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof value === 'string') map[key] = value
  }
  return Object.keys(map).length > 0 ? map : undefined
}

export function CompNode({ id, data }: NodeProps<Node<CompNodeData, 'comp'>>) {
  const studio = useContext(StudioContext)
  const { projectPath, setError } = studio
  const inputs = data.inputs ?? []
  const [luaSource, setLuaSource] = useState<string | null>(null)
  const [inputMap, setInputMap] = useState<Record<string, string> | undefined>()

  useEffect(() => {
    if (!data.luaPath || !projectPath) {
      setLuaSource(null)
      setInputMap(undefined)
      return
    }
    const luaPath = data.luaPath
    let cancelled = false
    void (async () => {
      try {
        const source = await invoke<string>('read_project_file', {
          project: projectPath,
          rel: luaPath,
        })
        if (cancelled) return
        setLuaSource(source)
        const jsonRel = luaPath.replace(/\.lua$/i, '.inputs.json')
        try {
          const json = await invoke<string>('read_project_file', {
            project: projectPath,
            rel: jsonRel,
          })
          if (!cancelled) setInputMap(parseInputsJson(json))
        } catch {
          if (!cancelled) setInputMap(undefined)
        }
      } catch (err) {
        if (!cancelled) {
          setLuaSource(null)
          setError(formatError(err))
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [data.luaPath, data.inputsTick, projectPath, setError])

  async function pick() {
    if (!studio.projectPath) {
      studio.setError('Open a project first')
      return
    }
    try {
      const src = await invoke<string | null>('pick_file', { extensions: ['lua'] })
      if (!src) return
      const luaPath = await invoke<string>('import_comp', {
        project: studio.projectPath,
        src,
      })
      const source = await invoke<string>('read_project_file', {
        project: studio.projectPath,
        rel: luaPath,
      })
      studio.patchNodeData(id, { luaPath, inputs: scanLuaInputs(source) })
      studio.setError(null)
    } catch (err) {
      studio.setError(formatError(err))
    }
  }

  return (
    <div className="studio-node">
      {inputs.map((input, index) => (
        <Handle
          key={input.name}
          type="target"
          position={Position.Left}
          id={input.name}
          className={`kind-${input.kind}`}
          style={{ top: 28 + index * 18 }}
          title={`${input.name} (${input.kind})`}
        />
      ))}
      <Handle type="source" position={Position.Right} id="comp" />
      <strong>Comp</strong>
      {studio.isStale(id) ? <div className="studio-node-badge">stale</div> : null}
      <div className="studio-node-path">{data.luaPath ?? 'No comp'}</div>
      {inputs.length > 0 ? (
        <ul className="studio-node-inputs">
          {inputs.map((input) => (
            <li key={input.name}>
              {input.name}: {input.kind}
            </li>
          ))}
        </ul>
      ) : null}
      {luaSource && isNativeOnly(luaSource) ? (
        <div className="studio-node-badge">native-only — use cadence preview</div>
      ) : null}
      {luaSource && shouldPreview(luaSource) ? (
        <WasmoonFrame
          luaSource={luaSource}
          inputs={inputMap}
          onError={(message) => studio.setError(message)}
        />
      ) : null}
      <button type="button" onClick={() => void pick()}>
        Pick…
      </button>
    </div>
  )
}
