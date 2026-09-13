// @vitest-environment jsdom
import { cloneElement, type ReactElement } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { TransparencyPanel, type TransparencyPanelProps } from './TransparencyPanel'
import { mockTransparencyDiagnostics } from '../model/transparency'

// Preserve the real chart and sourced ticks while supplying jsdom's missing layout.
vi.mock('recharts', async importOriginal => {
  const real = await importOriginal<typeof import('recharts')>()
  return { ...real, ResponsiveContainer: ({ children }: { children: ReactElement<{ width: number; height: number }> }) => cloneElement(children, { width: 620, height: 225 }) }
})

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({ matches: true, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  })
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
})
afterEach(cleanup)

function props(): TransparencyPanelProps {
  return {
    confidence: {
      level: 'Medium',
      score: { value: 0.5, source_type: 'assumption', ref: 'mock://confidence/score' },
      basis: 'illustrative_fixture_not_ensemble_inference',
      source: { source_type: 'assumption', ref: 'mock://confidence/basis' },
    },
    tariff: {
      operator: 'SPP',
      service: 'CHILLS (mock; not extracted)',
      curtailment_triggers: [{
        text: 'Illustrative system-reliability trigger; not extracted contract language.',
        observable: false,
        source: { source_type: 'assumption', ref: 'mock://tariff/trigger' },
      }],
    },
    siteExposure: { value: 0.4, source_type: 'assumption', ref: 'user://scenario/site_exposure' },
    onClose: vi.fn(),
  }
}

function readProvenance() {
  return JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!)
}

describe('transparency panel', () => {
  it('makes the system/site distinction and uncomputed diagnostics explicit', () => {
    render(<TransparencyPanel {...props()} />)
    const region = screen.getByRole('region', { name: 'What supports this estimate' })
    expect(region.textContent).toContain('local transmission headroom we do not have')
    expect(region.textContent).toContain('not a fitted coefficient or a measured site risk')
    expect(region.textContent).toContain('Confidence describes support for the estimate, not the probability')
    expect(region.textContent).toContain('Evaluation unavailable')
    expect(region.textContent).toContain('No held-out evaluation has been performed')
    expect(region.textContent).toContain('independent of the exposure slider')
    expect(screen.getByText('Assumed reliability curve')).toBeTruthy()
    expect(screen.getByText('Perfect calibration reference')).toBeTruthy()
    expect(screen.getByText('Mean predicted grid-stress probability (fraction)')).toBeTruthy()
    expect(screen.getByText('Observed grid-stress frequency (fraction)')).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('exposes exact provenance for every diagnostic score, bin, and axis tick', () => {
    const config = props()
    render(<TransparencyPanel {...config} />)
    const scores = [
      ['Assumed Brier score, not computed', mockTransparencyDiagnostics.brier_score],
      ['Assumed naive Brier score, not computed', mockTransparencyDiagnostics.naive_brier_score],
    ] as const
    for (const [label, value] of scores) {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }))
      expect(readProvenance()).toEqual(value)
      fireEvent.keyDown(document, { key: 'Escape' })
    }
    const bins = within(screen.getByRole('table', { name: 'Assumed reliability bins · fractions' })).getAllByRole('button')
    const expected = mockTransparencyDiagnostics.reliability_curve.flatMap(point => [point.mean_predicted, point.observed_fraction])
    expect(bins).toHaveLength(expected.length)
    bins.forEach((button, index) => {
      fireEvent.click(button)
      expect(readProvenance()).toEqual(expected[index])
      expect(readProvenance().ref).toContain('no held-out evaluation performed')
      fireEvent.keyDown(document, { key: 'Escape' })
    })
    const chart = screen.getByRole('group', { name: /Assumed reliability curve for system stress/ })
    const ticks = within(chart).getAllByRole('button')
    expect(ticks).toHaveLength(10)
    ticks.forEach(tick => {
      fireEvent.keyDown(tick, { key: 'Enter' })
      expect(readProvenance().source_type).toBe('assumption')
      expect(readProvenance().ref).toContain('fraction scale, not an observation')
      expect([0, 0.25, 0.5, 0.75, 1]).toContain(readProvenance().value)
      fireEvent.keyDown(document, { key: 'Escape' })
    })
    expect(config.onClose).not.toHaveBeenCalled()
  })

  it('updates the visible assumption and its provenance without changing the mock diagnostics', () => {
    const config = props()
    const { rerender } = render(<TransparencyPanel {...config} />)
    fireEvent.click(screen.getByRole('button', { name: /Site exposure factor assumption/ }))
    expect(readProvenance()).toEqual(config.siteExposure)
    const updated = { ...config.siteExposure, value: 0.65 }
    rerender(<TransparencyPanel {...config} siteExposure={updated} />)
    expect(readProvenance()).toEqual(updated)
    expect(screen.getByRole('button', { name: /Assumed Brier score, not computed: 0.2/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Site exposure factor assumption: 0.65/ })).toBeTruthy()
  })

  it('labels mock tariff records as unextracted and supplies no fabricated citation link', () => {
    const config = props()
    render(<TransparencyPanel {...config} />)
    expect(screen.getByText('Tariff evidence not supplied')).toBeTruthy()
    expect(screen.queryByText(config.tariff.curtailment_triggers[0].text)).toBeNull()
    expect(screen.getByText('SPP · Service terms not verified')).toBeTruthy()
    expect(screen.getByText('No verified citation available.')).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Tariff clause provenance/ }))
    expect(readProvenance()).toEqual({ value: config.tariff.curtailment_triggers[0].text, ...config.tariff.curtailment_triggers[0].source })
  })

  it('shows a missing-evidence state when no extracted triggers have arrived', () => {
    const config = props()
    render(<TransparencyPanel {...config} tariff={{ ...config.tariff, curtailment_triggers: [] }} />)
    expect(screen.getByText(/No extracted clauses have been supplied/)).toBeTruthy()
    expect(screen.getByText('SPP · Service terms not verified')).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it.each([
    ['clause', 'javascript:alert(1)'],
    ['clause', 'data:text/html,unsafe'],
    ['clause', 'https://name:password@example.invalid/tariff'],
    ['assumption', 'https://example.invalid/unverified'],
    ['model', 'https://example.invalid/not-a-clause'],
  ] as const)('keeps %s reference %s as text, not an external link', (sourceType, ref) => {
    const config = props()
    config.tariff.curtailment_triggers[0].source = { source_type: sourceType, ref }
    render(<TransparencyPanel {...config} />)
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText(`Supplied reference: ${ref}`)).toBeTruthy()
  })

  it.each(['https:', 'http:'])('preserves a supplied %s clause URL and its full provenance', protocol => {
    const config = props()
    const ref = `${protocol}//example.invalid/tariff.pdf#page=8`
    config.tariff.curtailment_triggers[0].source = { source_type: 'clause', ref }
    render(<TransparencyPanel {...config} />)
    const link = screen.getByRole('link', { name: 'Open supplied clause citation' })
    expect(link.getAttribute('href')).toBe(ref)
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    fireEvent.click(screen.getByRole('button', { name: /Tariff clause provenance/ }))
    expect(readProvenance().ref).toBe(ref)
  })

  it('closes with its button or Escape, and removes its Escape listener after unmount', () => {
    const config = props()
    const { unmount } = render(<TransparencyPanel {...config} />)
    fireEvent.click(screen.getByRole('button', { name: 'Close model and evidence' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(config.onClose).toHaveBeenCalledTimes(2)
    unmount()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(config.onClose).toHaveBeenCalledTimes(2)
  })
})
