import { useCallback, useEffect, useRef, useState } from 'react'

type DragState = { id: string; parentId: string } | null
export type KeyframeGapPreview = { parentId: string; gapIndex: number }

/** Pointer-based keyframe drag between word gaps (HTML5 DnD is unreliable in inline layouts). */
export function useKeyframeDrag(onMove: (id: string, gapIndex: number) => void) {
  const drag = useRef<DragState>(null)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [previewGap, setPreviewGap] = useState<KeyframeGapPreview | null>(null)

  const startDrag = useCallback((id: string, parentId: string) => {
    drag.current = { id, parentId }
    setDraggingId(id)
  }, [])

  const endDrag = useCallback(() => {
    drag.current = null
    setDraggingId(null)
    setPreviewGap(null)
  }, [])

  useEffect(() => {
    if (!draggingId) return

    const onMoveEvt = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-kf-gap]')
      if (!(el instanceof HTMLElement)) return
      const parentId = el.dataset.parentId
      const gapIndex = Number(el.dataset.gapIndex)
      if (parentId !== d.parentId || Number.isNaN(gapIndex)) return
      setPreviewGap({ parentId, gapIndex })
      onMove(d.id, gapIndex)
    }

    const onUp = () => endDrag()

    window.addEventListener('pointermove', onMoveEvt)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointermove', onMoveEvt)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [draggingId, onMove, endDrag])

  return { draggingId, previewGap, startDrag, endDrag }
}
