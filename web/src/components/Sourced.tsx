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
  description?: string
  /** Short display copy when the full description includes machine references. */
  summary?: string
  /** The exact text rendered on screen; defaults to `value` when the caller
   * shows the raw value verbatim. Required whenever visible text differs from
   * `value` (e.g. "p50" for value 50, or a formatted "$8.81M" for a raw
   * dollar figure) so the accessible name contains the visible text per
   * WCAG 2.5.3 (Label in Name). */
  displayText?: string
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

/** Intentional source inspection shared by values, input annotations, and SVG ticks. */
function useProvenance({ value, source, label, description, summary, displayText }: ProvenanceProps) {
  const id = useId()
  const anchor = useRef<HTMLElement | SVGElement | null>(null)
  const popover = useRef<HTMLDivElement | null>(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<CSSProperties>({ left: 12, top: 12, visibility: 'hidden' })
  const json = JSON.stringify({ value, source_type: source.source_type, ref: source.ref }, null, 2)
  // Full references remain in control metadata and the scenario export without
  // opening a raw JSON panel over the workspace.
  // The name must contain the visible text (WCAG 2.5.3), which is `displayText`
  // when the caller renders something other than the raw value verbatim.
  const accessibleLabel = `${label ? `${label}: ` : ''}${displayText ?? value}. ${source.source_type} provenance. Activate for source details.`
  const visibleDescription = summary ?? description
  const dismiss = useCallback(() => setOpen(false), [])

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
      const width = Math.min(visibleDescription ? 280 : 160, window.innerWidth - margin * 2)
      // Size only the concise source description; raw references never render here.
      popover.current.style.width = `${width}px`
      const height = popover.current.getBoundingClientRect().height
      const left = Math.max(margin, Math.min(bounds.left + bounds.width / 2 - width / 2, window.innerWidth - width - margin))
      const availableBelow = window.innerHeight - bounds.bottom - gap - margin
      const preferredTop = availableBelow >= height ? bounds.bottom + gap : bounds.top - height - gap
      const top = Math.max(margin, Math.min(preferredTop, window.innerHeight - height - margin))
      setPosition({ left, top, width, visibility: 'visible' })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open, visibleDescription])

  const toggle = () => setOpen((previous) => !previous)

  const keyboard = (event: KeyboardEvent<SVGGElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      toggle()
    }
  }

  return {
    attachAnchor: (node: HTMLElement | SVGElement | null) => { anchor.current = node },
    triggerProps: {
      'aria-label': accessibleLabel,
      'aria-describedby': open ? id : undefined,
      'aria-pressed': open,
      'data-provenance': json,
      'data-provenance-description': description,
      onClick: toggle,
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
          data-provenance={json}
          style={{ ...position, position: 'fixed', zIndex: 1000 }}
        >
          <div className="provenance-heading">
            <span>{source.source_type}</span>
          </div>
          {visibleDescription && <p className="provenance-description">{visibleDescription}</p>}
        </div>,
        document.body,
      )
      : null,
  }
}

/** Plain-text children only (e.g. 'p' + 50 -> 'p50'); null for JSX element
 * children, which the caller's own `label` is trusted to describe instead. */
function plainText(node: ReactNode): string | null {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) {
    const parts = node.map(plainText)
    return parts.every((part) => part !== null) ? parts.join('') : null
  }
  return null
}

/** Numeric UI primitive. The original, unrounded value is retained in provenance. */
export function Sourced({ value, source, format = defaultFormat, className = '', animate = true, children, label, description, summary }: SourcedProps) {
  const animated = useAnimatedNumber(typeof value === 'number' ? value : 0, animate && typeof value === 'number')
  // Match the visible span exactly when it's plain text, using the settled
  // (not mid-animation) formatted value - never the raw value. Complex JSX
  // children (icons, conditional badges) fall back to the caller's `label`.
  const displayText = children != null
    ? plainText(children)
    : (typeof value === 'number' ? format(value) : value)
  const provenance = useProvenance({ value, source, label, description, summary, displayText: displayText ?? undefined })
  return (
    <>
      <button
        type="button"
        ref={provenance.attachAnchor}
        className={`source-wrap ${className}`}
        data-value-type={typeof value}
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
export function SourceInfo({ value, source, label, description, summary }: SourceInfoProps) {
  const provenance = useProvenance({ value, source, label, description, summary })
  return (
    <>
      <button
        type="button"
        ref={provenance.attachAnchor}
        className={`source-mark source-${source.source_type}`}
        {...provenance.triggerProps}
      >
        <span aria-hidden="true">{source.source_type[0]}</span>
      </button>
      {provenance.layer}
    </>
  )
}

/** Recharts ticks retain exact source metadata and keyboard source inspection. */
export function SourcedTick({ x = 0, y = 0, payload, source, prefix = '', suffix = '', axis = 'x' }: SourcedTickProps) {
  const value = payload?.value ?? ''
  const label = `${prefix}${typeof value === 'number' ? defaultFormat(value) : value}${suffix}`
  const provenance = useProvenance({ value, source, displayText: label })
  return (
    <>
      <g
        ref={provenance.attachAnchor}
        transform={`translate(${x}, ${y})`}
        className="source-tick"
        role="button"
        tabIndex={0}
        style={{ cursor: 'help', fontVariantNumeric: 'tabular-nums' }}
        {...provenance.triggerProps}
        onKeyDown={provenance.keyboard}
      >
        <title>{`${label} · ${source.source_type}`}</title>
        <text x={axis === 'y' ? -4 : 0} y={axis === 'x' ? 16 : 4} textAnchor={axis === 'x' ? 'middle' : 'end'} fill="currentColor">
          {label}
        </text>
      </g>
      {provenance.layer}
    </>
  )
}
