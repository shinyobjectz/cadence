import * as Tabs from '@radix-ui/react-tabs'
import { FolderOpen, Moon, Save, Sun } from 'lucide-react'
import { useCallback, useState } from 'react'
import { Tip, TooltipProvider } from '../components/ui/tooltip'
import { cn } from '../lib/utils'
import { PreviewPanel } from './PreviewPanel'
import { SceneEditor } from './SceneEditor'
import { TimelineBar } from './TimelineBar'
import { TranscriptEditor } from './TranscriptEditor'
import { useEditor } from './store'
import { usePanelResize } from './usePanelResize'

export function EditorShell() {
  const leftTab = useEditor((s) => s.leftTab)
  const setLeftTab = useEditor((s) => s.setLeftTab)
  const theme = useEditor((s) => s.theme)
  const toggleTheme = useEditor((s) => s.toggleTheme)
  const projectName = useEditor((s) => s.projectName)
  const projectPath = useEditor((s) => s.projectPath)
  const dirty = useEditor((s) => s.dirty)
  const voResolving = useEditor((s) => s.voResolving)
  const openProjectPicker = useEditor((s) => s.openProjectPicker)
  const saveProject = useEditor((s) => s.saveProject)
  const { width: panelWidth, onPointerDown: onResizeDown } = usePanelResize()
  const [ioError, setIoError] = useState<string | null>(null)

  const onOpen = useCallback(async () => {
    setIoError(null)
    try {
      await openProjectPicker()
    } catch (err) {
      setIoError(err instanceof Error ? err.message : String(err))
    }
  }, [openProjectPicker])

  const onSave = useCallback(async () => {
    setIoError(null)
    try {
      await saveProject()
    } catch (err) {
      setIoError(err instanceof Error ? err.message : String(err))
    }
  }, [saveProject])

  return (
    <TooltipProvider>
      <div
        data-theme={theme}
        className={cn(
          'flex h-full w-full flex-col overflow-hidden bg-background text-foreground',
          theme === 'dark' ? 'dark' : '',
        )}
      >
        <div className="flex min-h-0 flex-1">
          {/* Left: document editors */}
          <aside
            style={{ width: panelWidth }}
            className="flex shrink-0 flex-col border-r border-border/40"
          >
            <Tabs.Root
              value={leftTab}
              onValueChange={(v) => setLeftTab(v as 'transcript' | 'scenes')}
              className="flex h-full flex-col"
            >
              <div className="flex items-center justify-between gap-2 px-3 pt-2 pb-1">
                <div className="flex min-w-0 items-center gap-1">
                  <Tip label="Open project">
                    <button
                      type="button"
                      onClick={() => void onOpen()}
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                      aria-label="Open project"
                    >
                      <FolderOpen size={16} />
                    </button>
                  </Tip>
                  <Tip label={projectPath ? 'Save doc.json' : 'Open a project to save'}>
                    <button
                      type="button"
                      onClick={() => void onSave()}
                      disabled={!projectPath}
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground disabled:opacity-30"
                      aria-label="Save project"
                    >
                      <Save size={16} />
                    </button>
                  </Tip>
                  <span className="truncate pl-1 text-xs text-muted-foreground">
                    {projectName}
                    {voResolving ? ' · voice…' : dirty ? ' •' : ''}
                  </span>
                </div>
                <Tabs.List className="inline-flex shrink-0 gap-0.5 rounded-lg bg-muted/40 p-0.5">
                  <Tabs.Trigger
                    value="transcript"
                    className={cn(
                      'rounded-md px-3 py-1.5 text-xs font-medium text-muted-foreground',
                      'data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm',
                    )}
                  >
                    Transcript
                  </Tabs.Trigger>
                  <Tabs.Trigger
                    value="scenes"
                    className={cn(
                      'rounded-md px-3 py-1.5 text-xs font-medium text-muted-foreground',
                      'data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm',
                    )}
                  >
                    Scenes
                  </Tabs.Trigger>
                </Tabs.List>
                <Tip label={theme === 'dark' ? 'Light mode' : 'Dark mode'}>
                  <button
                    type="button"
                    onClick={toggleTheme}
                    className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                    aria-label="Toggle theme"
                  >
                    {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
                  </button>
                </Tip>
              </div>
              {ioError ? (
                <p className="truncate px-3 pb-1 text-[10px] text-destructive">{ioError}</p>
              ) : null}
              <Tabs.Content value="transcript" className="flex-1 min-h-0 outline-none">
                <TranscriptEditor />
              </Tabs.Content>
              <Tabs.Content value="scenes" className="flex-1 min-h-0 outline-none">
                <SceneEditor />
              </Tabs.Content>
            </Tabs.Root>
          </aside>

          {/* Resize handle */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize panels"
            onPointerDown={onResizeDown}
            className={cn(
              'group relative z-10 w-1 shrink-0 cursor-col-resize',
              'bg-border/30 hover:bg-accent/40 active:bg-accent/60',
            )}
          >
            <div className="absolute inset-y-0 -left-1 -right-1" />
          </div>

          {/* Right: preview */}
          <main className="flex min-w-0 flex-1 flex-col">
            <PreviewPanel />
          </main>
        </div>

        {/* Bottom: full-width timeline */}
        <TimelineBar />
      </div>
    </TooltipProvider>
  )
}
