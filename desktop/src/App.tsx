import { isTauri } from '@tauri-apps/api/core'
import { useEffect } from 'react'
import { defaultDemoProjectPath } from './cadence/api'
import { EditorShell } from './editor/EditorShell'
import { useEditor } from './editor/store'

export default function App() {
  const loadProject = useEditor((s) => s.loadProject)
  const projectPath = useEditor((s) => s.projectPath)

  useEffect(() => {
    if (!isTauri() || projectPath) return
    void (async () => {
      try {
        const path = await defaultDemoProjectPath()
        await loadProject(path)
      } catch (err) {
        console.warn('cadence: could not auto-load launch-spot demo', err)
      }
    })()
  }, [loadProject, projectPath])

  return <EditorShell />
}
