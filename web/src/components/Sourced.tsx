import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import type { Source } from '../model'
import { useAnimatedNumber } from '../hooks/useAnimatedNumber'

type ProvenanceProps = {
  value: number | string
  source: Source
  label?: string
}

export type SourcedProps = ProvenanceProps & {
  format?: (value: number) => string
  className?: string
  animate?: boolean
  children?: ReactNode
}

export type SourceInfoProps = ProvenanceProps

export type SourcedTickProps = {
  x?: number
  y?: number
  payload?: { value: number | string }
  source: Source
  prefix?: string
  suffix?: string
  axis?: 'x' | 'y'
}

const defaultFormat = (value: number) =>
  value.toLocaleString('en-US', { maximumFractionDigits: 2 })

/** One popover behavior shared by inline values, input annotations, and SVG ticks. */
function useProvenance({ value, source, label }: ProvenanceProps) {
  const id = useId()
  const anchor = useRef<HTMLElement | SVGElement | null>(null)
  const popover = useRef<HTMLDivElement | null>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [position, setPosition] = useState<CSSProperties>({ left: 12, top: 12, visibility: 'hidden' })
  const open = !dismissed && (hovered || focused || pinned)
  const json = JSON.stringify({ value, source_type: source.source_type, ref: source.ref }, null, 2)
  // Full references can be long scenario URLs; expose them in the described JSON,
  // keeping the control's accessible name short enough to navigate efficiently.
  const accessibleLabel = `${label ? `${label}: ` : ''}${value}. ${source.source_type} provenance. Activate to pin.`

  const clearCloseTimer = useCallback(() => {
    if (closeTimer.current !== undefined) clearTimeout(closeTimer.current)
    closeTimer.current = undefined
  }, [])

  useEffect(() => clearCloseTimer, [clearCloseTimer])

  const enter = useCallback(() => {
    clearCloseTimer()
    setHovered(true)
    setDismissed(false)
  }, [clearCloseTimer])

  const leave = useCallback(() => {
    clearCloseTimer()
    // Allow the pointer to cross the small gap into the portal without losing it.
    closeTimer.current = setTimeout(() => setHovered(false), 120)
  }, [clearCloseTimer])

  const dismiss = useCallback(() => {
    clearCloseTimer()
    setHovered(false)
    setFocused(false)
    setPinned(false)
    setDismissed(true)
  }, [clearCloseTimer])

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Node && !anchor.current?.contains(target) && !popover.current?.contains(target)) {
        dismiss()
      }
    }
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') dismiss()
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open, dismiss])

  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      if (!anchor.current || !popover.current) return
      const margin = 12
      const gap = 8
      const bounds = anchor.current.getBoundingClientRect()
      const width = Math.min(360, window.innerWidth - margin * 2)
      // Measure at the final constrained width so wrapped references cannot clip.
      popover.current.style.width = `${width}px`
      popover.current.style.maxHeight = `${window.innerHeight - margin * 2}px`
      const height = popover.current.getBoundingClientRect().height
      const left = Math.max(margin, Math.min(bounds.left + bounds.width / 2 - width / 2, window.innerWidth - width - margin))
      const availableBelow = window.innerHeight - bounds.bottom - gap - margin
      const preferredTop = availableBelow >= height ? bounds.bottom + gap : bounds.top - height - gap
      const top = Math.max(margin, Math.min(preferredTop, window.innerHeight - height - margin))
      setPosition({ left, top, width, maxHeight: window.innerHeight - margin * 2, visibility: 'visible' })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open, json, pinned])

  const togglePin = () => {
    setDismissed(false)
    setPinned((previous) => !previous)
  }

  const focus = () => {
    setFocused(true)
    setDismissed(false)
  }

  const keyboard = (event: KeyboardEvent<SVGGElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      togglePin()
    }
  }

  return {
    attachAnchor: (node: HTMLElement | SVGElement | null) => { anchor.current = node },
    anchorEvents: { onMouseEnter: enter, onMouseLeave: leave },
    triggerProps: {
      'aria-label': accessibleLabel,
      'aria-describedby': open ? id : undefined,
      'aria-pressed': pinned,
      onFocus: focus,
      onBlur: () => setFocused(false),
      onClick: togglePin,
    },
    keyboard,
    json,
    layer: open && typeof document !== 'undefined'
      ? createPortal(
        <div
          ref={popover}
          id={id}
          role="tooltip"
          className="provenance-popover"
          style={{ ...position, position: 'fixed', zIndex: 1000, overflow: 'auto' }}
          onMouseEnter={enter}
          onMouseLeave={leave}
        >
          <div className="provenance-heading">
            <span>{source.source_type}</span>
            <span>{pinned ? 'Pinned' : 'Provenance'}</span>
          </div>
          <pre className="provenance-json" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{json}</pre>
          <span className="provenance-hint">{pinned ? 'Escape or click outside to dismiss' : 'Activate the value or its source tag to pin'}</span>
        </div>,
        document.body,
      )
      : null,
  }
}

/** Numeric UI primitive. The original, unrounded value is retained in provenance. */
export function Sourced({ value, source, format = defaultFormat, className = '', animate = true, children, label }: SourcedProps) {
  const animated = useAnimatedNumber(typeof value === 'number' ? value : 0, animate && typeof value === 'number')
  const provenance = useProvenance({ value, source, label })
  return (
    <>
      <button
        type="button"
        ref={provenance.attachAnchor}
        className={`source-wrap ${className}`}
        {...provenance.anchorEvents}
        {...provenance.triggerProps}
      >
        <span className="sourced-value" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {children ?? (typeof value === 'number' ? format(animated) : value)}
        </span>
        <span className={`source-mark source-${source.source_type}`} aria-hidden="true">{source.source_type[0]}</span>
      </button>
      {provenance.layer}
    </>
  )
}

/** A compact provenance control placed beside a numeric input or range. */
export function SourceInfo({ value, source, label }: SourceInfoProps) {
  const provenance = useProvenance({ value, source, label })
  return (
    <>
      <button
        type="button"
        ref={provenance.attachAnchor}
        className={`source-mark source-${source.source_type}`}
        {...provenance.anchorEvents}
        {...provenance.triggerProps}
      >
        <span aria-hidden="true">{source.source_type[0]}</span>
      </button>
      {provenance.layer}
    </>
  )
}

/** Recharts ticks stay valid SVG while their full provenance is rendered in a portal. */
export function SourcedTick({ x = 0, y = 0, payload, source, prefix = '', suffix = '', axis = 'x' }: SourcedTickProps) {
  const value = payload?.value ?? ''
  const label = `${prefix}${typeof value === 'number' ? defaultFormat(value) : value}${suffix}`
  const provenance = useProvenance({ value, source, label })
  return (
    <>
      <g
        ref={provenance.attachAnchor}
        transform={`translate(${x}, ${y})`}
        className="source-tick"
        role="button"
        tabIndex={0}
        style={{ cursor: 'help', fontVariantNumeric: 'tabular-nums' }}
        {...provenance.anchorEvents}
        {...provenance.triggerProps}
        onKeyDown={provenance.keyboard}
      >
        <title>{provenance.json}</title>
        <text x={axis === 'y' ? -4 : 0} y={axis === 'x' ? 16 : 4} textAnchor={axis === 'x' ? 'middle' : 'end'} fill="currentColor">
          {label}
        </text>
      </g>
      {provenance.layer}
    </>
  )
}
