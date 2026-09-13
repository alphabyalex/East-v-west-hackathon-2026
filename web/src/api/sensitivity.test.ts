import { describe, expect, it, vi } from 'vitest'
import { validateEconomicsAssumptions } from './assumptions'
import { buildEstimateSensitivity } from './sensitivity'
import snapshot from '../model/economics-assumptions.json'
import { createMockEstimate, defaultInputs, toEstimateRequest } from '../model'
import { buildSensitivity } from '../model/sensitivity'

const assumptions = validateEconomicsAssumptions(snapshot)
const request = toEstimateRequest(defaultInputs)
const reply = (body: unknown) => new Response(JSON.stringify(body), { status: 200 })

describe('API-backed sensitivity', () => {
  it.each([0.01, 0.4, 1])('uses the received exposure at site factor %s without another HTTP request', async factor => {
    const inputs = { ...defaultInputs, site_exposure: factor }
    const response = createMockEstimate(toEstimateRequest(inputs))
    const original = structuredClone(response)
    const fetchImpl = vi.fn()
    const actual = await buildEstimateSensitivity(response, assumptions, { fetchImpl })
    const expected = buildSensitivity(response, createMockEstimate({ ...request, site_exposure: 1 }), inputs, assumptions)
    expect(fetchImpl).not.toHaveBeenCalled()
    expect(response).toEqual(original)
    for (const row of actual.rows) {
      const reference = expected.rows.find(entry => entry.key === row.key)!
      expect(row.status).toBe(reference.status)
      if (row.status !== 'modeled' || reference.status !== 'modeled') continue
      expect(row.low.delta_value_usd.value).toBeCloseTo(reference.low.delta_value_usd.value, 5)
      expect(row.high.delta_value_usd.value).toBeCloseTo(reference.high.delta_value_usd.value, 5)
      expect(row.low.snapshot.decision).toBe(reference.low.snapshot.decision)
      expect(row.high.snapshot.decision).toBe(reference.high.snapshot.decision)
    }
  })

  it('requests the identical scenario at full exposure only when the current response is zero-scaled', async () => {
    const response = createMockEstimate({ ...request, site_exposure: 0 })
    const full = createMockEstimate({ ...request, site_exposure: 1 })
    const fetchImpl = vi.fn().mockResolvedValue(reply(full))
    const controller = new AbortController()
    const result = await buildEstimateSensitivity(response, assumptions, { fetchImpl, signal: controller.signal })
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    const [url, options] = fetchImpl.mock.calls[0]
    expect(url).toMatch(/\/api\/estimate$/)
    expect(JSON.parse(options.body)).toEqual({ ...request, site_exposure: 1 })
    expect(Object.keys(JSON.parse(options.body))).toHaveLength(5)
    expect(options.signal).toBe(controller.signal)
    const site = result.rows.find(row => row.key === 'site_exposure')!
    expect(site.status).toBe('modeled')
    if (site.status !== 'modeled') throw new Error('Expected modeled site sensitivity')
    expect(site.high.delta_value_usd.value).toBeLessThan(0)
    expect(site.low.delta_value_usd.value).toBe(0)
  })

  it('keeps flexibility sensitivity meaningful when current interruption costs are zero', async () => {
    const response = createMockEstimate({ ...request, flexibility_split: 0 })
    const result = await buildEstimateSensitivity(response, assumptions)
    const flexibility = result.rows.find(row => row.key === 'flexibility_split')!
    expect(flexibility.status).toBe('modeled')
    if (flexibility.status !== 'modeled') throw new Error('Expected modeled flexibility sensitivity')
    expect(flexibility.high.delta_value_usd.value).toBeLessThan(0)
    expect(flexibility.low.snapshot.breakeven_hours.value).toBeNull()
  })

  it.each(['bad echo', 'changed economics', 'changed exposure version', 'changed confidence', 'unavailable'])(
    'rejects a zero-factor companion with %s instead of mixing sources', async fault => {
      const response = createMockEstimate({ ...request, site_exposure: 0 })
      const full = createMockEstimate({ ...request, site_exposure: 1 })
      if (fault === 'bad echo') full.inputs_echo.load_mw += 1
      if (fault === 'changed economics') full.economics.annual_cost_usd.p50 += 100
      if (fault === 'changed exposure version') full.modeled_exposure.source.ref += '&model_version=changed'
      if (fault === 'changed confidence') full.confidence.score += 0.1
      const fetchImpl = fault === 'unavailable'
        ? vi.fn().mockRejectedValue(new TypeError('Server unavailable'))
        : vi.fn().mockResolvedValue(reply(full))
      await expect(buildEstimateSensitivity(response, assumptions, { fetchImpl })).rejects.toThrow()
    },
  )
})
