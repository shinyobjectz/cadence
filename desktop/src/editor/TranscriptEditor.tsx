import { useCallback, useEffect, useMemo, useRef } from 'react'
import {
  GapInserter,
  KeyframeDragPreview,
  KeyframeHandle,
  MidGapSpacing,
} from './KeyframeGlyph'
import { lineKeyframesFor, useEditor } from './store'
import { formatTimecode, keyframeTime } from './time'
import { voEndTime } from './editorial'
import { transcriptPlayheadAt } from './transcriptPlayhead'
import type { KeyframeKind, TranscriptLine } from './types'
import { useKeyframeDrag } from './useKeyframeDrag'
import { cn } from '../lib/utils'

function gapsForLine(line: TranscriptLine) {
  return line.words.length + 1
}

function isBetweenWords(gapIndex: number, wordCount: number) {
  return gapIndex > 0 && gapIndex < wordCount
}

function gapUsesMidSpacing(atGap: { kind: KeyframeKind }[]) {
  return atGap.some((k) => k.kind === 'mid')
}

function GapPreview({
  isLeading,
  betweenWords,
  isTrailing,
}: {
  isLeading: boolean
  betweenWords: boolean
  isTrailing: boolean
}) {
  if (betweenWords) {
    return (
      <>
        <MidGapSpacing />
        <KeyframeDragPreview />
        <MidGapSpacing />
      </>
    )
  }
  if (isLeading || isTrailing) {
    return <KeyframeDragPreview />
  }
  return null
}

/** Vertical playhead caret for word gaps and post-VO regions. */
function PlayheadCaret({
  className,
  ...rest
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      {...rest}
      className={cn(
        'pointer-events-none inline-block w-px shrink-0 self-stretch bg-accent align-middle',
        className,
      )}
      aria-hidden
    />
  )
}

export function TranscriptEditor() {
  const doc = useEditor((s) => s.doc)
  const keyframes = doc.lineKeyframes
  const fps = doc.fps
  const playhead = useEditor((s) => s.playhead)
  const playing = useEditor((s) => s.playing)
  const scrubbing = useEditor((s) => s.scrubbing)
  const selectedId = useEditor((s) => s.selectedKeyframeId)
  const selectKeyframe = useEditor((s) => s.selectKeyframe)
  const addLineKeyframe = useEditor((s) => s.addLineKeyframe)
  const moveLineKeyframe = useEditor((s) => s.moveLineKeyframe)
  const setPlayhead = useEditor((s) => s.setPlayhead)

  const scrollRef = useRef<HTMLDivElement>(null)
  const atPlayhead = useMemo(() => transcriptPlayheadAt(doc, playhead), [doc, playhead])
  const voEnd = useMemo(() => voEndTime(doc), [doc])
  const editorialScenes = useMemo(
    () => doc.scenes.filter((s) => s.at >= voEnd - 0.001).sort((a, b) => a.at - b.at),
    [doc.scenes, voEnd],
  )
  const lockSelection = playing || scrubbing

  const onMove = useCallback(
    (id: string, gapIndex: number) => moveLineKeyframe(id, gapIndex),
    [moveLineKeyframe],
  )
  const { draggingId, previewGap, startDrag, endDrag } = useKeyframeDrag(onMove)

  const onGapClick = useCallback(
    (line: TranscriptLine, gapIndex: number, e: React.MouseEvent) => {
      e.stopPropagation()
      const kind: KeyframeKind = e.altKey ? 'close' : e.shiftKey ? 'open' : 'mid'
      addLineKeyframe(line.id, gapIndex, kind)
      setPlayhead(keyframeTime(kind, line.words, gapIndex))
    },
    [addLineKeyframe, setPlayhead],
  )

  useEffect(() => {
    if (!lockSelection) return
    const prevent = (e: Event) => e.preventDefault()
    document.addEventListener('selectstart', prevent)
    return () => document.removeEventListener('selectstart', prevent)
  }, [lockSelection])

  useEffect(() => {
    if (!lockSelection || !scrollRef.current) return
    let selector = ''
    if (atPlayhead?.kind === 'word') {
      selector = `[data-word-id="${atPlayhead.wordId}"]`
    } else if (atPlayhead?.kind === 'keyframe') {
      selector = `[data-kf-id="${atPlayhead.keyframeId}"]`
    } else if (atPlayhead?.kind === 'gap') {
      selector = `[data-gap-playhead="${atPlayhead.lineId}-${atPlayhead.gapIndex}"]`
    } else if (atPlayhead?.kind === 'editorial' && atPlayhead.sceneId) {
      selector = `[data-editorial-id="${atPlayhead.sceneId}"]`
    }
    if (!selector) return
    const el = scrollRef.current.querySelector(selector)
    if (el instanceof HTMLElement) {
      el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' })
    }
  }, [atPlayhead, lockSelection])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div
        ref={scrollRef}
        className={cn(
          'flex-1 overflow-auto no-scrollbar px-1 py-3 font-mono text-[13px] leading-7',
          lockSelection && 'select-none',
        )}
      >
        {doc.lines.map((line) => {
          const lineStart = line.words[0]?.start ?? 0
          const lineKfs = lineKeyframesFor(line.id, keyframes)
          const gaps = gapsForLine(line)
          const lastGap = line.words.length

          return (
            <div
              key={line.id}
              className="group flex items-start gap-0 min-h-7 rounded-sm hover:bg-muted/30"
              onClick={() => setPlayhead(lineStart)}
            >
              <div
                className="w-[88px] shrink-0 select-none pr-3 pt-px text-right text-muted-foreground tabular-nums"
                aria-hidden
              >
                {formatTimecode(lineStart, fps)}
              </div>
              <div className="relative min-w-0 flex-1 flex flex-wrap items-baseline gap-y-1 leading-7">
                {Array.from({ length: gaps }, (_, gapIndex) => {
                  const atGap = lineKfs.filter((k) => k.gapIndex === gapIndex)
                  const isLeading = gapIndex === 0
                  const isTrailing = gapIndex === lastGap
                  const betweenWords = isBetweenWords(gapIndex, line.words.length)
                  const isPreviewGap =
                    draggingId !== null &&
                    previewGap?.parentId === line.id &&
                    previewGap.gapIndex === gapIndex
                  const midSpacing =
                    gapUsesMidSpacing(atGap) || (isPreviewGap && betweenWords)
                  const showGapCaret =
                    atPlayhead?.kind === 'gap' &&
                    atPlayhead.lineId === line.id &&
                    atPlayhead.gapIndex === gapIndex

                  return (
                    <span
                      key={gapIndex}
                      data-kf-gap
                      data-parent-id={line.id}
                      data-gap-index={gapIndex}
                      className="inline align-middle"
                    >
                      <GapInserter
                        label={
                          isLeading
                            ? 'Click: mid · Shift: open · Alt: close'
                            : 'Add keyframe between words'
                        }
                        dropTarget={isPreviewGap}
                        onClick={(e) => onGapClick(line, gapIndex, e)}
                      />
                      {showGapCaret ? (
                        <PlayheadCaret
                          className="mx-0.5 h-[1.1em] animate-pulse"
                          data-gap-playhead={`${line.id}-${gapIndex}`}
                        />
                      ) : null}
                      {atGap.map((kf) => {
                        const isActiveKf =
                          atPlayhead?.kind === 'keyframe' &&
                          atPlayhead.keyframeId === kf.id
                        return (
                          <span key={kf.id} data-kf-id={kf.id} className="inline align-middle">
                            <KeyframeHandle
                              kind={kf.kind}
                              selected={selectedId === kf.id || isActiveKf}
                              leading={isLeading && kf.kind === 'open'}
                              trailing={isTrailing && kf.kind === 'close'}
                              betweenWords={betweenWords}
                              dragging={draggingId === kf.id}
                              onPointerDown={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                startDrag(kf.id, line.id)
                                selectKeyframe(kf.id)
                              }}
                              onPointerUp={endDrag}
                              onPointerCancel={endDrag}
                              onClick={(e) => {
                                e.stopPropagation()
                                selectKeyframe(kf.id)
                                setPlayhead(keyframeTime(kf.kind, line.words, kf.gapIndex))
                              }}
                            />
                          </span>
                        )
                      })}
                      {isPreviewGap ? (
                        <GapPreview
                          isLeading={isLeading}
                          betweenWords={betweenWords}
                          isTrailing={isTrailing}
                        />
                      ) : null}
                      {gapIndex < line.words.length ? (
                        <WordSpan
                          word={line.words[gapIndex]!}
                          active={
                            atPlayhead?.kind === 'word' &&
                            atPlayhead.wordId === line.words[gapIndex]!.id
                          }
                          padLeft={gapIndex > 0 && !midSpacing}
                        />
                      ) : null}
                    </span>
                  )
                })}
              </div>
            </div>
          )
        })}
        {editorialScenes.map((scene) => {
          const active =
            atPlayhead?.kind === 'editorial' && atPlayhead.sceneId === scene.id
          return (
            <div
              key={scene.id}
              data-editorial-id={scene.id}
              className={cn(
                'group flex items-start min-h-7 rounded-sm hover:bg-muted/30',
                active && 'bg-amber-500/5',
              )}
              onClick={() => setPlayhead(scene.at)}
            >
              <div
                className="w-[88px] shrink-0 select-none pr-3 pt-px text-right tabular-nums text-amber-600 dark:text-amber-400"
                aria-hidden
              >
                {formatTimecode(scene.at, fps)}
              </div>
              <div
                className={cn(
                  'min-w-0 flex-1 break-words pt-px text-amber-700 dark:text-amber-400',
                  active && 'font-medium',
                )}
              >
                {scene.id}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const WordSpan = ({
  word,
  active,
  padLeft,
}: {
  word: { id: string; text: string }
  active?: boolean
  padLeft?: boolean
}) => (
  <span
    data-word-id={word.id}
    className={cn(
      'inline rounded-sm px-0.5 text-foreground',
      padLeft && 'ml-1',
      active && 'ring-1 ring-accent ring-offset-1 ring-offset-background',
    )}
  >
    {word.text}
  </span>
)
