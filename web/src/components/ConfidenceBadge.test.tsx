// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ConfidenceBadge, type ConfidenceEstimate } from './ConfidenceBadge'
import { SourceInfo, SourcedTick } from './Sourced'

afterEach(cleanup)

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  })
})

const mockConfidence: ConfidenceEstimate = {
  level: 'Medium',
  score: { value: 0.5, source_type: 'assumption', ref: 'mock://confidence/score' },
  basis: 'illustrative_placeholder',
  source: { source_type: 'assumption', ref: 'mock://confidence/basis' },
}

const modelConfidence: ConfidenceEstimate = {
  level: 'High',
  score: { value: 0.623456, source_type: 'model', ref: 'pipeline/confidence.py model_version=test_fixture' },
  basis: 'ensemble_disagreement',
  source: { source_type: 'model', ref: 'n_similar_historical_hours=340' },
}

function readProvenance() {
  return JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!)
}

describe('confidence provenance', () => {
  it('retains the confidence level and opens its qualification only on activation, without extra badges', () => {
    render(<ConfidenceBadge confidence={mockConfidence} />)
    expect(screen.getByText('Confidence: Medium')).toBeTruthy()
    expect(screen.queryByText('Assumed')).toBeNull()
    expect(document.querySelector('.mock-label, .source-wrap .source-mark')).toBeNull()
    const badge = screen.getByRole('button', { name: /Assumed estimate confidence: Medium. Signal score: 0.5/ })
    fireEvent.focus(badge)
    expect(screen.queryByRole('tooltip')).toBeNull()
    expect(JSON.parse(badge.getAttribute('data-provenance')!)).toEqual(mockConfidence.score)
    fireEvent.click(badge)
    expect(readProvenance()).toEqual(mockConfidence.score)
    expect(screen.getByRole('tooltip').textContent).toContain('Assumed confidence input; no ensemble evaluation is available for this value')
    expect(screen.getByRole('tooltip').textContent).not.toContain('illustrative_placeholder')
    expect(screen.getByRole('tooltip').textContent).not.toContain('mock://')
    expect(badge.getAttribute('data-provenance-description')).toContain('illustrative_placeholder')
    expect(badge.getAttribute('data-provenance-description')).toContain(mockConfidence.source.ref)
    expect(badge.getAttribute('aria-pressed')).toBe('true')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('preserves the model level, exact score and basis metadata without opening on hover', () => {
    render(<ConfidenceBadge confidence={modelConfidence} />)
    expect(screen.getByText('Confidence: High')).toBeTruthy()
    expect(screen.queryByText('Assumed')).toBeNull()
    expect(document.body.textContent).not.toContain('%')
    const badge = screen.getByRole('button', { name: /Estimate confidence: High. Signal score: 0.623456. model provenance/ })
    expect(badge.querySelector('.source-mark')).toBeNull()
    fireEvent.mouseEnter(badge)
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.click(badge)
    expect(readProvenance()).toEqual(modelConfidence.score)
    expect(badge.getAttribute('data-provenance-description')).toContain('ensemble_disagreement')
    expect(badge.getAttribute('data-provenance-description')).toContain('n_similar_historical_hours=340')
    expect(screen.getByRole('tooltip').textContent).not.toContain('n_similar_historical_hours=340')
    expect(screen.getByRole('tooltip').textContent).toContain('support for the estimate, not the probability of a future outcome')
  })

  it.each(['score', 'source'] as const)('retains the confidence qualification when only the %s ref is illustrative', (field) => {
    const confidence: ConfidenceEstimate = {
      ...modelConfidence,
      [field]: { ...modelConfidence[field], ref: 'mock://mixed-response' },
    }
    render(<ConfidenceBadge confidence={confidence} />)
    expect(screen.queryByText('Assumed')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Assumed estimate confidence/ }))
    expect(screen.getByRole('tooltip').textContent).toContain('no ensemble evaluation is available for this value')
    expect(readProvenance()).toEqual(confidence.score)
  })

  it('updates inspected source metadata and level when a different model result arrives', () => {
    const { rerender } = render(<ConfidenceBadge confidence={mockConfidence} />)
    fireEvent.click(screen.getByRole('button'))
    rerender(<ConfidenceBadge confidence={modelConfidence} />)
    expect(screen.getByText('Confidence: High')).toBeTruthy()
    expect(screen.queryByText('Assumed')).toBeNull()
    expect(readProvenance()).toEqual(modelConfidence.score)
  })

  it('keeps the supplied level in compact rows and exact source details accessible without an Assumed pill', () => {
    render(<ConfidenceBadge confidence={mockConfidence} compact />)
    expect(screen.getByText('Medium')).toBeTruthy()
    expect(screen.queryByText('Assumed')).toBeNull()
    expect(document.querySelector('.mock-label, .source-wrap .source-mark')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Assumed estimate confidence/ }))
    expect(readProvenance()).toEqual(mockConfidence.score)
  })

  it('removes standalone info icons while retaining source metadata and clickable chart values', () => {
    const { rerender, container } = render(<SourceInfo value={modelConfidence.score.value} source={modelConfidence.score} />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(container.querySelector('svg, .source-mark')).toBeNull()
    expect(JSON.parse(container.querySelector('[data-source-metadata]')!.getAttribute('data-provenance')!)).toEqual(modelConfidence.score)
    rerender(<svg><SourcedTick payload={{ value: 123 }} source={modelConfidence.source} /></svg>)
    const tick = screen.getByRole('button', { name: /model provenance/ })
    fireEvent.focus(tick)
    fireEvent.mouseEnter(tick)
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.keyDown(tick, { key: 'Enter' })
    expect(readProvenance()).toEqual({ value: 123, ...modelConfidence.source })
    expect(JSON.parse(tick.getAttribute('data-provenance')!)).toEqual({ value: 123, ...modelConfidence.source })
    expect(tick.querySelector('title')?.textContent).toBe('123')
    fireEvent.keyDown(tick, { key: ' ' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.keyDown(tick, { key: ' ' })
    expect(readProvenance()).toEqual({ value: 123, ...modelConfidence.source })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
