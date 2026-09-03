import { cn } from '../lib/utils'
import { activeSceneAt } from './editorial'
import { ParamChip } from './ParamChip'
import { useEditor } from './store'
import { formatTimecode } from './time'
import type { SceneBlock } from './types'

function SceneBlockView({ block, active }: { block: SceneBlock; active: boolean }) {
  const fps = useEditor((s) => s.doc.fps)
  const params = useEditor((s) => s.doc.sceneParams)
  const setPlayhead = useEditor((s) => s.setPlayhead)

  return (
    <article
      className={cn(
        'group flex items-start gap-3 border-b border-border/25 px-4 py-4 last:border-b-0 hover:bg-muted/20',
        active && 'bg-accent/5 ring-1 ring-inset ring-accent/30',
      )}
      onClick={() => setPlayhead(block.at)}
    >
      <button
        type="button"
        className={cn(
          'shrink-0 self-start pt-px w-[76px] text-right',
          'font-mono text-[11px] tabular-nums leading-relaxed text-muted-foreground',
          'hover:text-foreground',
        )}
        onClick={(e) => {
          e.stopPropagation()
          setPlayhead(block.at)
        }}
      >
        {formatTimecode(block.at, fps)}
      </button>

      <div className="min-w-0 flex-1 text-[13px] leading-[1.65] text-foreground/90">
        <p className="m-0 text-left [text-wrap:pretty] [word-spacing:normal]">
          {block.segments.map((seg, idx) =>
            seg.type === 'text' ? (
              <span key={idx} className="select-none">
                {seg.content}
              </span>
            ) : (
              <ParamChip key={idx} param={params[seg.paramId]!} />
            ),
          )}
        </p>
      </div>
    </article>
  )
}

export function SceneEditor() {
  const scenes = useEditor((s) => s.doc.scenes)
  const playhead = useEditor((s) => s.playhead)
  const doc = useEditor((s) => s.doc)
  const active = activeSceneAt(doc, playhead)

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex-1 overflow-auto no-scrollbar py-1">
        {scenes.map((block) => (
          <SceneBlockView key={block.id} block={block} active={active?.id === block.id} />
        ))}
      </div>
    </div>
  )
}
