import { Tip } from '../components/ui/tooltip'
import { cn } from '../lib/utils'
import type { KeyframeKind } from './types'

const KEYFRAME_TIPS: Record<KeyframeKind, string> = {
  open: 'Open — anchors to the start of the next word',
  close: 'Close — anchors to the end of the previous word',
  mid: 'Mid — halfway between adjacent words',
}

type KeyframeGlyphProps = {
  kind: KeyframeKind
  selected?: boolean
  trailing?: boolean
  leading?: boolean
  className?: string
}

/** Keyframe marks from designer SVGs — diamond (mid), play arrows (open/close). */
export function KeyframeGlyph({
  kind,
  selected,
  trailing,
  leading,
  className,
}: KeyframeGlyphProps) {
  const spacing =
    kind === 'open'
      ? cn('ml-0 -mr-0.5 translate-x-px', leading && '-mr-1')
      : kind === 'close'
        ? cn('-ml-0.5 mr-0 -translate-x-px', trailing && '-ml-1')
        : 'mx-0'

  const shell = cn(
    'inline-block shrink-0 align-middle text-accent',
    kind === 'mid' ? 'h-2.5 w-2.5' : 'h-2.5 w-[9px]',
    spacing,
    selected && 'text-accent-foreground drop-shadow-[0_0_4px_var(--accent)]',
    className,
  )

  if (kind === 'mid') {
    return (
      <svg viewBox="0 0 78 78" className={shell} aria-hidden>
        <rect
          x="38.857"
          y="-5.38478"
          width="62.5673"
          height="62.5673"
          rx="13"
          transform="rotate(45 38.857 -5.38478)"
          fill="currentColor"
        />
      </svg>
    )
  }

  if (kind === 'open') {
    return (
      <svg viewBox="0 0 68 75" className={shell} aria-hidden>
        <path
          fill="currentColor"
          d="M61.5 26.0098C70.1667 31.0135 70.1667 43.5227 61.5 48.5264L19.5 72.7752C10.8333 77.7789 -4.31002e-08 71.5242 3.94337e-07 61.5168L2.51423e-06 13.0194C2.95166e-06 3.01199 10.8333 -3.24264 19.5 1.76106L61.5 26.0098Z"
        />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 68 75" className={shell} aria-hidden>
      <path
        fill="currentColor"
        d="M6.5 48.5264C-2.16667 43.5227 -2.16667 31.0135 6.5 26.0098L48.5 1.76107C57.1667 -3.24264 68 3.01199 68 13.0194L68 61.5168C68 71.5242 57.1667 77.7789 48.5 72.7752L6.5 48.5264Z"
      />
    </svg>
  )
}

/** One half of duplicated word-gap spacing (use on both sides of a mid keyframe). */
export function MidGapSpacing() {
  return <span className="inline-block w-[0.35em]" aria-hidden />
}

/** Vertical snap indicator shown while dragging a keyframe. */
export function KeyframeDragPreview() {
  return (
    <span
      className={cn(
        'pointer-events-none inline-block h-[1.1em] w-px shrink-0 align-middle',
        'bg-accent shadow-[0_0_4px_var(--accent)]',
      )}
      aria-hidden
    />
  )
}

/** Zero-width gap hit target — never shifts surrounding text. */
export function GapInserter({
  label,
  onClick,
  dropTarget,
}: {
  label: string
  onClick: (e: React.MouseEvent) => void
  dropTarget?: boolean
}) {
  return (
    <Tip label={label}>
      <button
        type="button"
        className="relative inline-block h-[1em] w-0 align-middle border-0 bg-transparent p-0 focus-visible:outline-none"
        onClick={onClick}
        aria-label="Insert keyframe"
      >
        <span
          className={cn(
            'pointer-events-none absolute left-0 top-1/2 h-4 w-1 -translate-y-1/2 rounded-sm',
            dropTarget
              ? 'bg-accent/60 opacity-100'
              : 'bg-muted/70 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
          )}
        />
      </button>
    </Tip>
  )
}

/** Keyframe drag handle — layout-neutral wrapper. */
export function KeyframeHandle({
  kind,
  selected,
  trailing,
  leading,
  betweenWords,
  dragging,
  onPointerDown,
  onPointerUp,
  onPointerCancel,
  onClick,
}: {
  kind: KeyframeKind
  selected?: boolean
  trailing?: boolean
  leading?: boolean
  /** Mid keyframe sitting between two words — symmetric gap spacing. */
  betweenWords?: boolean
  dragging?: boolean
  onPointerDown: (e: React.PointerEvent) => void
  onPointerUp: (e: React.PointerEvent) => void
  onPointerCancel: (e: React.PointerEvent) => void
  onClick: (e: React.MouseEvent) => void
}) {
  const showMidGap = kind === 'mid' && betweenWords

  return (
    <Tip label={KEYFRAME_TIPS[kind]}>
      <button
        type="button"
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onClick={onClick}
        className={cn(
          'inline touch-none border-0 bg-transparent px-1 leading-none align-middle',
          dragging ? 'cursor-grabbing opacity-35' : 'cursor-grab',
        )}
        aria-label={KEYFRAME_TIPS[kind]}
      >
        {showMidGap ? <MidGapSpacing /> : null}
        <KeyframeGlyph
          kind={kind}
          selected={selected}
          trailing={trailing}
          leading={leading}
        />
        {showMidGap ? <MidGapSpacing /> : null}
      </button>
    </Tip>
  )
}
