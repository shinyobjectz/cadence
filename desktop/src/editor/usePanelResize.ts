import { useCallback, useEffect, useRef, useState } from 'react'

const MIN = 280
const MAX = 720
const DEFAULT = 420

export function usePanelResize() {
  const [width, setWidth] = useState(DEFAULT)
  const dragging = useRef(false)

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    dragging.current = true
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }, [])

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return
      setWidth((w) => Math.max(MIN, Math.min(MAX, w + e.movementX)))
    }
    const onUp = () => {
      dragging.current = false
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  return { width, onPointerDown }
}
