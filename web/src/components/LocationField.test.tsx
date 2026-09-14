// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScenarioProvider, useScenario } from '../ScenarioContext'
import { createMockEstimate, defaultInputs, toEstimateRequest, type EstimateRequest, type EstimateResponse } from '../model'
import economicSnapshot from '../model/economics-assumptions.json'
import { LocationField } from './LocationField'

// Catalog membership is authored test data, not a claim that every zone has a
// production-ready annual estimate. The real provider may still return 404.
const zones = ['CSWS', 'EDE', 'GRDA', 'INDN', 'KACY', 'KCPL', 'LES', 'MPS', 'NPPD',
  'OKGE', 'OPPD', 'SECI', 'SPRM', 'SPS', 'WAUE', 'WFEC', 'WR']
const scenarios = [
  { id: 'spp-wichita-demo', label: 'Wichita, KS · scenario', kind: 'scenario' },
  { id: 'spp-oklahoma-city-demo', label: 'Oklahoma City, OK · scenario', kind: 'scenario' },
  { id: 'spp-lincoln-demo', label: 'Lincoln, NE · scenario', kind: 'scenario' },
]
const catalog = [
  { id: 'SPP_SYSTEM', label: 'SPP system aggregate', kind: 'system' },
  ...zones.map(id => ({ id, label: `${id} · SPP load zone`, kind: 'zone' })),
  ...scenarios,
]

function serverFixture(request: EstimateRequest): EstimateResponse {
  // Reuse the contract fixture's consistent economics without inventing a real
  // zone model. Its original mock location is replaced only in this test response.
  const response = createMockEstimate({ ...request, location_id: 'SPP_SYSTEM' })
  response.inputs_echo = { ...request }
  response.modeled_exposure.source = {
    source_type: 'assumption', ref: `test://authored-location-response/${request.location_id}/exposure`,
  }
  response.economics.source = {
    source_type: 'assumption', ref: `test://authored-location-response/${request.location_id}/economics`,
  }
  response.confidence = {
    level: 'Low', score: 0.27, basis: 'authored_test_response_not_pipeline_evidence',
    source: { source_type: 'assumption', ref: `test://authored-location-response/${request.location_id}/confidence` },
  }
  return response
}

const json = (value: unknown) => new Response(JSON.stringify(value), {
  status: 200, headers: { 'Content-Type': 'application/json' },
})

function installServer({
  locations = () => Promise.resolve(json({ locations: catalog })),
  estimate = (request: EstimateRequest) => Promise.resolve(json(serverFixture(request))),
}: {
  locations?: () => Promise<Response>
  estimate?: (request: EstimateRequest) => Promise<Response>
} = {}) {
  const requests: EstimateRequest[] = []
  const fetcher = vi.fn<typeof fetch>().mockImplementation((url, options) => {
    const path = new URL(String(url), 'http://127.0.0.1').pathname
    if (path === '/api/locations') {
      expect(options?.method ?? 'GET').toBe('GET')
      return locations()
    }
    if (path === '/api/economics-assumptions') return Promise.resolve(json(economicSnapshot))
    if (path !== '/api/estimate' || options?.method !== 'POST') {
      throw new Error(`Unexpected test transport: ${String(url)}`)
    }
    const request = JSON.parse(String(options.body)) as EstimateRequest
    requests.push(request)
    return estimate(request)
  })
  vi.stubGlobal('fetch', fetcher)
  return { fetcher, requests }
}

function Probe() {
  const { inputs, result, mode, status, chooseMode } = useScenario()
  return <>
    <output data-testid="scenario-state">{JSON.stringify({ inputs, response: result.canonical_response, mode, status })}</output>
    <button onClick={() => chooseMode('local')}>Use local scenario</button>
    <button onClick={() => chooseMode('api')}>Use connected scenario</button>
  </>
}

function mount(initialMode: 'api' | 'local' = 'api') {
  return render(<ScenarioProvider initialMode={initialMode}><LocationField showTelemetry={false} setShowTelemetry={() => {}} /><Probe /></ScenarioProvider>)
}

function state(): { inputs: { location_id: string }; response: EstimateResponse; mode: string; status: string } {
  return JSON.parse(screen.getByTestId('scenario-state').textContent!)
}

function select() { return screen.getByRole('combobox', { name: /SPP LOCATION/ }) as HTMLSelectElement }
function optionIds() { return Array.from(select().options, option => option.value) }
async function tick(ms = 50) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('location catalog and scenario transport', () => {
  it('loads all 18 actual IDs plus three clearly separate scenario choices from GET', async () => {
    const { fetcher } = installServer()
    mount()
    expect(optionIds()).toEqual(['custom-location', 'SPP_SYSTEM', ...scenarios.map(option => option.id)])
    await tick()
    expect(optionIds()).toEqual(['custom-location', ...catalog.map(option => option.id)])
    for (const zone of zones) expect(screen.getByRole('option', { name: `${zone} · SPP load zone` })).toBeTruthy()
    for (const scenario of scenarios) expect(screen.getByRole('option', { name: scenario.label })).toBeTruthy()
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/api/locations'))).toHaveLength(1)
    expect(screen.getByText(/no site-specific grid data/)).toBeTruthy()
  })

  it.each(['CSWS', 'OKGE', 'LES'])('posts the exact %s ID and consumes the source-bearing test server response', async id => {
    const { requests } = installServer()
    mount()
    await tick()
    fireEvent.change(select(), { target: { value: id } })
    expect(screen.getByText('SPP load zone · zone-specific model data; site exposure is your assumption')).toBeTruthy()
    expect(screen.queryByText(/no site-specific grid data/)).toBeNull()
    expect(state().inputs.location_id).toBe(id)
    expect(state().response.inputs_echo.location_id).toBe(id)
    await tick()
    const expected = { ...toEstimateRequest(defaultInputs), location_id: id }
    expect(requests.at(-1)).toEqual(expected)
    expect(Object.keys(requests.at(-1)!)).toHaveLength(6)
    expect(state().status).toBe('api')
    expect(state().response).toEqual(serverFixture(expected))
    expect(state().response.modeled_exposure.source).toEqual(serverFixture(expected).modeled_exposure.source)
    expect(select().value).toBe(id)
  })

  it('accepts a future ID supplied by the catalog without a hardcoded frontend zone list', async () => {
    const next = { id: 'FUTURE_ZONE_42', label: 'Future zone from test catalog', kind: 'zone' }
    const { requests } = installServer({ locations: () => Promise.resolve(json({ locations: [...catalog, next] })) })
    mount()
    await tick()
    expect(screen.getByRole('option', { name: next.label })).toBeTruthy()
    fireEvent.change(select(), { target: { value: next.id } })
    await tick()
    expect(requests.at(-1)?.location_id).toBe(next.id)
    expect(state().status).toBe('api')
    expect(state().response.inputs_echo.location_id).toBe(next.id)
    expect(state().response.modeled_exposure.source.ref).toContain(`/authored-location-response/${next.id}/`)
  })

  it('keeps the original choices usable if the catalog request fails without failing a valid estimate', async () => {
    installServer({ locations: () => Promise.reject(new TypeError('Synthetic catalog outage')) })
    mount()
    await tick()
    expect(optionIds()).toEqual(['custom-location', 'SPP_SYSTEM', ...scenarios.map(option => option.id)])
    expect(screen.getByText(/Location list unavailable/i)).toBeTruthy()
    expect(state().status).toBe('api')
    fireEvent.change(select(), { target: { value: scenarios[0].id } })
    await tick()
    expect(state().response.inputs_echo.location_id).toBe(scenarios[0].id)
    expect(state().status).toBe('api')
  })

  it('keeps the selected zone and explicit mock provenance while its estimate is pending and after a 404', async () => {
    let finish!: (response: Response) => void
    const pending = new Promise<Response>(resolve => { finish = resolve })
    const { requests } = installServer({ estimate: request => request.location_id === 'CSWS'
      ? pending : Promise.resolve(json(serverFixture(request))) })
    mount()
    await tick()
    fireEvent.change(select(), { target: { value: 'CSWS' } })
    expect(state().status).toBe('loading')
    expect(state().response.inputs_echo.location_id).toBe('CSWS')
    expect(state().response.modeled_exposure.source.source_type).toBe('assumption')
    expect(state().response.modeled_exposure.source.ref).toMatch(/^mock:/)
    expect(state().response.modeled_exposure.source.ref).toContain('CSWS')
    await tick()
    expect(requests.at(-1)?.location_id).toBe('CSWS')
    await act(async () => { finish(new Response('Test-only annual coverage unavailable', { status: 404 })) })
    expect(state().status).toBe('fallback')
    expect(select().value).toBe('CSWS')
    expect(state().inputs.location_id).toBe('CSWS')
    expect(state().response.inputs_echo.location_id).toBe('CSWS')
    for (const source of [state().response.modeled_exposure.source, state().response.confidence.source, state().response.economics.source]) {
      expect(source.source_type).toBe('assumption')
      expect(source.ref).toMatch(/^mock:/)
    }
  })

  it('keeps a loaded catalog and selected zone when switching to local mode without further requests', async () => {
    const { fetcher } = installServer()
    mount()
    await tick()
    fireEvent.change(select(), { target: { value: 'LES' } })
    await tick()
    fireEvent.click(screen.getByRole('button', { name: 'Use local scenario' }))
    const calls = fetcher.mock.calls.length
    await tick(4000)
    expect(fetcher).toHaveBeenCalledTimes(calls)
    expect(optionIds()).toEqual(['custom-location', ...catalog.map(option => option.id)])
    expect(select().value).toBe('LES')
    expect(state().mode).toBe('local')
    expect(state().response.inputs_echo.location_id).toBe('LES')
    expect(state().response.modeled_exposure.source.ref).toMatch(/^mock:/)
  })

  it('uses only the original offline choices and makes no requests when initially local', async () => {
    const { fetcher } = installServer()
    mount('local')
    await tick(4000)
    expect(fetcher).not.toHaveBeenCalled()
    expect(optionIds()).toEqual(['custom-location', 'SPP_SYSTEM', ...scenarios.map(option => option.id)])
    expect(state().status).toBe('local')
  })
})
