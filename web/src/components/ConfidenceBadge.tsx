import type { Source, SourcedValue } from '../model'
import { Sourced } from './Sourced'
import { useRef } from 'react'
import { useChangeMotion } from '../hooks/useChangeMotion'
import { isMockSource } from './MockLabel'

export interface ConfidenceEstimate {
  level: 'High' | 'Medium' | 'Low'
  score: SourcedValue
  basis: string
  source: Source
}

export interface ConfidenceBadgeProps {
  confidence: ConfidenceEstimate
  compact?: boolean
}

/** The supplied model level is shown verbatim; the UI assigns no score thresholds. */
export function ConfidenceBadge({ confidence, compact = false }: ConfidenceBadgeProps) {
  const mocked = isMockSource(confidence.source) || isMockSource(confidence.score)
  const levelRef = useRef<HTMLSpanElement>(null)
  useChangeMotion(levelRef, confidence.level)
  const label = `${mocked ? 'Mock estimate' : 'Estimate'} confidence: ${confidence.level}. Signal score`
  const description = [
    mocked
      ? 'Mock confidence input for interface development.'
      : 'Model signal describing support for the exposure estimate.',
    `Basis: ${confidence.basis}.`,
    `Confidence basis source (${confidence.source.source_type}): ${confidence.source.ref}.`,
    'Confidence describes support for the estimate, not the probability of a future outcome.',
  ].join(' ')

  return (
    <Sourced
      value={confidence.score.value}
      source={confidence.score}
      label={label}
      description={description}
      className={`confidence-badge${compact ? ' confidence-compact' : ''}`}
      animate={false}
    >
      <span ref={levelRef}>{compact ? confidence.level : `Confidence: ${confidence.level}`}</span>
      {mocked && <span className="confidence-mock mock-label">Mock</span>}
    </Sourced>
  )
}
