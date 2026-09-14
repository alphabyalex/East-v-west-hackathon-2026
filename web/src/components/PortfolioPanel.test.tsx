// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PortfolioPanel } from './PortfolioPanel'
import { validatePortfolio, type Portfolio, type PortfolioSite } from '../api/portfolio'
import { createMockEstimate, defaultInputs } from '../model'
import manifest from '../../../data/processed/national_stack/zone_rankings.json'

const state = vi.hoisted(() => ({ inputs: {} }))
vi.mock('../ScenarioContext', () => ({ useScenario: () => state }))
const source = { source_type: 'assumption' as const, ref: 'user://portfolio/inputs' }
const empty: Portfolio = {
  id: 'session', checked_at_utc: '2026-09-13T12:00:00Z', storage: 'Single worker memory only.',
  check_mode: 'Static checks; no live monitoring.', economics_basis: 'Server defaults.', ranking_basis: 'Published zone scores.', sites: [],
}
function site(location: string, name = location): PortfolioSite {
  return {
    id: location, name, inputs: { location_id: location, load_mw: 100, term_years: 7, flexibility_split: .6, site_exposure: .5, vpp_solar_homes: 500 },
    input_source: source, thresholds: { exposure_p90_hours_above: null, confidence_score_below: null },
    ranking: { status: 'unavailable', zone_rank: null, composite_score: null, reasons: ['Annual exposure and carbon evidence missing.'], source },
    wind_evidence: (manifest.available_wind_evidence.find(w => w.location_id === location) ?? null) as PortfolioSite['wind_evidence'],
    estimate: null, estimate_unavailable_reason: 'Annual reference is not ready.',
    threshold_status: { mode: 'static_precomputed_check', status: 'not_configured', checks: [] },
  }
}

describe('session portfolio', () => {
  beforeEach(() => {
    state.inputs = { ...defaultInputs, location_id: 'LES', vpp_solar_homes: 500 }
    sessionStorage.clear()
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('loads saved selections, compares real wind evidence/exclusions, stores thresholds, reloads and removes', async () => {
    let saved = { ...structuredClone(empty), sites: [site('LES', 'First site'), site('EDE', 'Next site')] }
    sessionStorage.setItem('fluxline_portfolio_session', 'session')
    const fetcher = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname
      if (init.method === 'POST' && path.endsWith('/sites')) {
        const body = JSON.parse(String(init.body))
        saved.sites.push({ ...site(body.inputs.location_id, body.name), inputs: body.inputs })
      } else if (init.method === 'PUT') {
        const thresholds = JSON.parse(String(init.body))
        const entry = saved.sites[0]; entry.thresholds = thresholds
        entry.threshold_status = { mode: 'static_precomputed_check', status: 'unavailable', checks: [{
          metric: 'exposure_p90_hours_above', threshold: thresholds.exposure_p90_hours_above, threshold_source: source,
          observed: null, observed_source: null, status: 'unavailable', reason: 'Annual reference is not ready.',
        }] }
      } else if (init.method === 'DELETE') saved.sites = saved.sites.filter(s => !path.endsWith(s.id))
      return new Response(JSON.stringify(saved), { status: init.method === 'POST' ? 201 : 200 })
    })
    vi.stubGlobal('fetch', fetcher)
    const view = render(<PortfolioPanel />)
    expect(screen.queryByText(/Static checks of current precomputed data/)).toBeNull()
    expect(screen.queryByLabelText('Portfolio site name')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save current site to portfolio' })).toBeNull()
    expect(screen.queryByText(/Uses current server economics assumptions/)).toBeNull()
    await screen.findByRole('button', { name: 'Remove First site' })
    expect(screen.getByRole('button', { name: /^764\./ })).toBeTruthy()
    expect(screen.getAllByText('Composite ranking unavailable')).toHaveLength(2)
    expect(screen.getByText('Wind evidence unavailable for this zone.')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Exposure threshold for First site'), { target: { value: '200' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save thresholds for First site' }))
    await screen.findByText('Threshold check unavailable')
    expect(screen.queryByText('Within saved thresholds')).toBeNull()
    expect(screen.getByRole('button', { name: /^200\./ })).toBeTruthy()
    view.unmount(); render(<PortfolioPanel />)
    await screen.findByRole('button', { name: 'Remove First site' })
    expect((screen.getByLabelText('Exposure threshold for First site') as HTMLInputElement).value).toBe('200')
    fireEvent.click(screen.getByRole('button', { name: 'Remove Next site' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove Next site' })).toBeNull())
    expect(sessionStorage.getItem('fluxline_portfolio_session')).toBe('session')
  })

  it('shows expired sessions and offers explicit restart without inventing saved entries', async () => {
    sessionStorage.setItem('fluxline_portfolio_session', 'expired')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Portfolio session expired.' }), { status: 404 })).mockResolvedValueOnce(new Response(JSON.stringify(empty))))
    render(<PortfolioPanel />)
    await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: 'Save current site to portfolio' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Start new portfolio session' }))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(screen.getByText('No saved sites in this session.')).toBeTruthy()
  })

  it('keeps a network failure visible and does not invent saved sites', async () => {
    sessionStorage.setItem('fluxline_portfolio_session', 'session')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('API offline')))
    render(<PortfolioPanel />)
    expect((await screen.findByRole('alert')).textContent).toContain('API offline')
    expect(screen.queryByRole('button', { name: 'Remove Site' })).toBeNull()
  })

  it('recovers the existing session after an initial outage instead of abandoning its saved sites', async () => {
    sessionStorage.setItem('fluxline_portfolio_session', 'session')
    const data = { ...empty, sites: [site('EDE', 'Existing site')] }
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('API offline'))
      .mockResolvedValueOnce(new Response(JSON.stringify(data)))

    vi.stubGlobal('fetch', fetcher)
    render(<PortfolioPanel />)
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh evidence and checks' }))
    await screen.findByRole('button', { name: 'Remove Existing site' })
    expect(fetcher.mock.calls.map(([url, init]) => [new URL(url).pathname, init.method])).toEqual([
      ['/api/portfolios/session', 'GET'], ['/api/portfolios/session', 'GET'],
    ])
  })

  it('rejects invented unavailable scores, malformed sources, and falsely healthy static checks', () => {
    const valid = { ...empty, sites: [site('LES')] }
    expect(validatePortfolio(valid)).toEqual(valid)
    const score = structuredClone(valid); score.sites[0].ranking.composite_score = .9
    expect(() => validatePortfolio(score)).toThrow('Invalid portfolio')
    const provenance = structuredClone(valid); provenance.sites[0].input_source.ref = ''
    expect(() => validatePortfolio(provenance)).toThrow('Invalid portfolio')
    const healthy = structuredClone(valid); healthy.sites[0].threshold_status.status = 'within_thresholds'
    expect(() => validatePortfolio(healthy)).toThrow('Invalid portfolio')
    const missing = structuredClone(valid); missing.sites[0].thresholds.exposure_p90_hours_above = 100
    expect(() => validatePortfolio(missing)).toThrow('Invalid portfolio')
  })

  it('renders an exceeded static check with sourced observed values for an authored supported fixture', async () => {
    const entry = site('LES')
    entry.estimate = createMockEstimate({ ...entry.inputs, location_id: 'SPP_SYSTEM' })
    entry.estimate.inputs_echo = entry.inputs
    const evidence = { source_type: 'model' as const, ref: 'test-fixture://authored annual-ready evidence; not production' }
    entry.estimate.modeled_exposure.source = evidence
    const observed = entry.estimate.modeled_exposure.p90
    entry.thresholds.exposure_p90_hours_above = observed - 1
    entry.threshold_status = { mode: 'static_precomputed_check', status: 'breached', checks: [{
      metric: 'exposure_p90_hours_above', threshold: observed - 1, threshold_source: source,
      observed, observed_source: evidence, status: 'breached', reason: 'Static precomputed fixture check.',
    }] }
    const data = { ...empty, sites: [entry] }
    expect(validatePortfolio(data)).toEqual(data)
    sessionStorage.setItem('fluxline_portfolio_session', 'session')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(data))))
    render(<PortfolioPanel />)
    await screen.findByText('Threshold exceeded')
    expect(screen.getByText(/Exceeded · current/)).toBeTruthy()
    expect(screen.getByText(/Costs are scenario calculations/)).toBeTruthy()
    const contradictory = structuredClone(data)
    contradictory.sites[0].threshold_status.checks[0].observed! += 1
    expect(() => validatePortfolio(contradictory)).toThrow('Invalid portfolio')
  })
})
