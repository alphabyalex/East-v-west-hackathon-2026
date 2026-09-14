// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PortfolioComparison } from './PortfolioComparison'
import type { Portfolio, PortfolioSite } from '../api/portfolio'
import { realGridFixture } from '../api/grid-impact-fixtures'
import { getGridImpact, powerScenario } from '../api/grid-impact'
vi.mock('../api/grid-impact', async original => ({ ...await original<typeof import('../api/grid-impact')>(), getGridImpact: vi.fn() }))
const source = { source_type: 'assumption' as const, ref: 'user://saved-scenario' }
function site(id: string, load: number): PortfolioSite {
  return { id, name: `${id} candidate`, inputs: { location_id: id, load_mw: load, term_years: 7, flexibility_split: .6, site_exposure: .4, vpp_solar_homes: 0 }, input_source: source,
    thresholds: { exposure_p90_hours_above: null, confidence_score_below: null }, ranking: { status: 'unavailable', zone_rank: null, composite_score: null, reasons: ['No annual input'], source },
    wind_evidence: null, estimate: null, estimate_unavailable_reason: 'Annual reference missing', threshold_status: { mode: 'static_precomputed_check', status: 'not_configured', checks: [] } }
}
const portfolio: Portfolio = { id: 'test', checked_at_utc: '2026-09-14T00:00:00Z', storage: 'session', check_mode: 'static', economics_basis: 'server', ranking_basis: 'published', sites: [site('LES', 100), site('OKGE', 1000), site('EDE', 100)] }
function row(key: string) { return within(document.querySelector(`[data-comparison-row="${key}"]`) as HTMLElement) }
beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.mocked(getGridImpact).mockImplementation(async zone => realGridFixture(zone))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('compares saved capacity and real price scenarios with exact provenance and honest missing carbon', async () => {
  render(<PortfolioComparison portfolio={portfolio} disabled={false} onRemove={vi.fn()} />)
  await waitFor(() => expect(row('value').getAllByRole('button').length).toBeGreaterThan(0))
  const les = realGridFixture('LES')
  if (les.cheap_power.status !== 'observed_hours_only') throw new Error('Expected committed price evidence')
  const expected = powerScenario(les.cheap_power, 100, .5).dollars
  const money = row('value').getAllByRole('button')[0]
  fireEvent.click(money)
  expect(JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!)).toEqual(expected)
  expect(expected.source_type).toBe('assumption')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(row('interruptible').getAllByRole('button', { name: /^60(?:\.| )/ })).toBeTruthy()
  expect(row('base').getAllByRole('button', { name: /^40(?:\.| )/ })).toBeTruthy()
  expect(row('value').getAllByText('Unavailable')).toHaveLength(1)
  expect(row('carbon').getAllByText('Unavailable')).toHaveLength(3)
  expect(screen.queryByText('Published ranking')).toBeNull()
  fireEvent.change(screen.getByLabelText('Comparison upward available capacity'), { target: { value: '0' } })
  expect(row('value').getAllByRole('button', { name: /^\$0(?:\.| )/ })).toHaveLength(2)
  expect(getGridImpact).toHaveBeenCalledTimes(3)
})
it('changes the reference, filters matching inputs, and retains working remove controls', async () => {
  const remove = vi.fn()
  render(<PortfolioComparison portfolio={portfolio} disabled={false} onRemove={remove} />)
  await waitFor(() => expect(row('value').queryByText('Loading...')).toBeNull())
  fireEvent.change(screen.getByLabelText('Comparison reference site'), { target: { value: 'OKGE' } })
  expect(row('load').getAllByRole('button', { name: /^-900(?:\.| )/ })).toHaveLength(2)
  fireEvent.click(screen.getByRole('button', { name: 'Differences only' }))
  expect(document.querySelector('[data-comparison-row="term"]')).toBeNull()
  expect(document.querySelector('[data-comparison-row="load"]')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Remove OKGE candidate' }))
  expect(remove).toHaveBeenCalledWith(portfolio.sites[1])
})
it('deduplicates requests and clears stale opportunity values when the portfolio changes', async () => {
  const first = { ...portfolio, sites: [site('LES', 100), { ...site('LES', 200), id: 'les-two' }] }
  const page = render(<PortfolioComparison portfolio={first} disabled={false} onRemove={vi.fn()} />)
  await waitFor(() => expect(row('value').getAllByRole('button').length).toBeGreaterThan(0))
  expect(getGridImpact).toHaveBeenCalledTimes(1)
  vi.mocked(getGridImpact).mockRejectedValue(new Error('Offline'))
  page.rerender(<PortfolioComparison portfolio={{ ...portfolio, sites: [site('EDE', 100)] }} disabled={false} onRemove={vi.fn()} />)
  expect(row('value').queryAllByRole('button')).toHaveLength(0)
  await waitFor(() => expect(row('value').getByText('Unavailable')).toBeTruthy())
})
it('does not calculate cross-period deltas when observation windows differ', async () => {
  vi.mocked(getGridImpact).mockImplementation(async zone => {
    const data = realGridFixture(zone)
    if (zone === 'OKGE') data.coverage.wind.period_start_utc = '2025-06-01T00:00:00Z'
    return data
  })
  render(<PortfolioComparison portfolio={portfolio} disabled={false} onRemove={vi.fn()} />)
  await waitFor(() => expect(row('value').getByText('Periods differ')).toBeTruthy())
  expect(row('value').getAllByRole('button')).toHaveLength(2)
})
