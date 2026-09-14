// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { withEconomics } from '../api/test-fixtures'
import { offlineLocations } from '../api/locations'
import type { Portfolio, PortfolioSite } from '../api/portfolio'

vi.mock('recharts', async original => ({ ...await original<typeof import('recharts')>(), ResponsiveContainer: () => <div /> }))
const sessionKey = 'fluxline_portfolio_session'
const source = { source_type: 'assumption' as const, ref: 'test-fixture://saved-inputs' }
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
let portfolio: Portfolio
let fetcher: ReturnType<typeof vi.fn>

function entry(body: { name: string; inputs: PortfolioSite['inputs'] }): PortfolioSite {
  return { ...body, id: `site-${portfolio.sites.length}`, input_source: source,
    thresholds: { exposure_p90_hours_above: null, confidence_score_below: null },
    ranking: { status: 'unavailable', zone_rank: null, composite_score: null, reasons: ['Annual evidence is insufficient.'], source },
    wind_evidence: null, estimate: null, estimate_unavailable_reason: 'Annual evidence is insufficient.',
    threshold_status: { mode: 'static_precomputed_check', status: 'not_configured', checks: [] } }
}

async function scenario(zone = 'CSWS') {
  render(<App />)
  fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }))
  await screen.findByRole('option', { name: 'CSWS · SPP zone' })
  fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: zone } })
  fireEvent.click(screen.getByRole('button', { name: 'Save to Portfolio' }))
}

describe('unified portfolio save', () => {
  beforeEach(() => {
    localStorage.clear(); sessionStorage.clear()
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api')
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }) })
    portfolio = { id: 'shared-session', checked_at_utc: '2026-09-14T07:00:00Z', storage: 'Session only', check_mode: 'Static', economics_basis: 'Server defaults', ranking_basis: 'Published evidence', sites: [] }
    fetcher = vi.fn(async (url: string, init: RequestInit = {}) => {
      const path = new URL(url).pathname
      if (path === '/api/locations') return response({ locations: [...offlineLocations, { id: 'CSWS', label: 'CSWS · SPP zone', kind: 'zone' }, { id: 'LES', label: 'LES · SPP zone', kind: 'zone' }] })
      if (path.endsWith('/sites') && init.method === 'POST') { portfolio.sites.push(entry(JSON.parse(String(init.body)))); return response(portfolio, 201) }
      if (path.startsWith('/api/portfolios')) return response(portfolio, init.method === 'POST' ? 201 : 200)
      return response({ detail: 'Evidence unavailable in this test.' }, 503)
    })
    const wrapped = withEconomics(fetcher)
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => url.endsWith('/api/locations') ? fetcher(url, init) : wrapped(url, init))
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

  it('saves header inputs into the backend session and displays them across tabs and reloads', async () => {
    await scenario()
    fireEvent.change(screen.getByLabelText('LOAD SIZE'), { target: { value: '250' } })
    fireEvent.change(screen.getByLabelText('VPP ORCHESTRATION'), { target: { value: '500' } })
    fireEvent.change(screen.getByRole('slider', { name: 'Site exposure factor' }), { target: { value: '.7' } })
    fireEvent.change(screen.getByLabelText('Site name'), { target: { value: '  CSWS candidate  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    await screen.findByText('Saved CSWS candidate to Portfolio & Alerts.')
    expect(portfolio.sites[0].inputs).toEqual({ location_id: 'CSWS', load_mw: 250, term_years: 7, flexibility_split: .6, site_exposure: .7, vpp_solar_homes: 500 })
    expect(sessionStorage.getItem(sessionKey)).toBe('shared-session')
    expect(screen.queryByRole('button', { name: 'Save Active Scenario' })).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: 'Portfolio & Alerts' }))
    await screen.findByRole('button', { name: 'Remove CSWS candidate' })
    const table = screen.getByRole('region', { name: 'Saved site comparison' })
    expect(within(table).getByText('CSWS')).toBeTruthy()
    expect(within(table).getByRole('button', { name: /^500\./ })).toBeTruthy()
    expect(within(table).getByText('Exposure and cost unavailable')).toBeTruthy()
    expect(screen.queryByLabelText('Site name')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }))
    await screen.findByRole('option', { name: 'LES · SPP zone' })
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'LES' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save to Portfolio' }))
    fireEvent.change(screen.getByLabelText('Site name'), { target: { value: 'LES candidate' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    await screen.findByText('Saved LES candidate to Portfolio & Alerts.')
    expect(fetcher.mock.calls.filter(([url, init]) => new URL(url).pathname === '/api/portfolios' && init.method === 'POST')).toHaveLength(1)
    cleanup(); render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: 'Portfolio & Alerts' }))
    await screen.findByRole('button', { name: 'Remove CSWS candidate' })
    expect(screen.getByRole('button', { name: 'Remove LES candidate' })).toBeTruthy()
  })

  it.each(['SPP_SYSTEM', 'spp-wichita-demo'])('explains why %s cannot be saved without making a portfolio request', async zone => {
    await scenario(zone)
    expect(screen.getByRole('alert').textContent).toContain('Select a real SPP zone before saving')
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    expect(fetcher.mock.calls.filter(([url]) => url.includes('/api/portfolios'))).toHaveLength(0)
  })

  it('shows inline validation for a blank name and keeps keyboard focus in the name field', async () => {
    await scenario()
    fireEvent.change(screen.getByLabelText('Site name'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    expect(screen.getByRole('alert').textContent).toBe('Enter a name to save this site.')
    expect(document.activeElement).toBe(screen.getByLabelText('Site name'))
    expect(fetcher.mock.calls.filter(([url]) => url.includes('/api/portfolios'))).toHaveLength(0)
  })

  it('retains a created session after save failure and retries without creating another session', async () => {
    const base = fetcher.getMockImplementation()!
    let fail = true
    fetcher.mockImplementation(async (url, init) => {
      if (url.endsWith('/sites') && fail) { fail = false; return response({ detail: 'Portfolio is full; remove a saved site first.' }, 409) }
      return base(url, init)
    })
    await scenario()
    fireEvent.change(screen.getByLabelText('Site name'), { target: { value: 'Retry site' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    await screen.findByText('Portfolio is full; remove a saved site first.')
    expect(screen.queryByText(/Saved Retry site/)).toBeNull()
    expect(sessionStorage.getItem(sessionKey)).toBe('shared-session')
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    await screen.findByText('Saved Retry site to Portfolio & Alerts.')
    expect(fetcher.mock.calls.filter(([url, init]) => new URL(url).pathname === '/api/portfolios' && init.method === 'POST')).toHaveLength(1)
  })

  it('recovers an expired session explicitly without discarding the name', async () => {
    sessionStorage.setItem(sessionKey, 'expired')
    const base = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, init) => url.includes('/expired/') ? Promise.resolve(response({ detail: 'Portfolio session not found or expired. Start a new session.' }, 404)) : base(url, init))
    await scenario()
    fireEvent.change(screen.getByLabelText('Site name'), { target: { value: 'Recovered site' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Start new portfolio session' }))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect((screen.getByLabelText('Site name') as HTMLInputElement).value).toBe('Recovered site')
    fireEvent.click(screen.getByRole('button', { name: 'Save site' }))
    await screen.findByText('Saved Recovered site to Portfolio & Alerts.')
  })
})
