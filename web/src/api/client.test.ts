import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EstimateRequest, EstimateResponse } from '../model/contract'
import { EstimateClientError, postEstimate, validateEstimateResponse } from './client'

const request: EstimateRequest = {
  location_id: 'SPP_SPS_HUB', load_mw: 100, term_years: 2, flexibility_split: 0.6, site_exposure: 0.3,
}

function fixture(): EstimateResponse {
  return {
    inputs_echo: { ...request },
    modeled_exposure: {
      unit: 'hours/year', p50: 30, p90: 90, p99: 150, worst_contiguous_outage_hours: 12,
      by_year: [
        { year: 1, p50: 20, p90: 80, p99: 140 },
        { year: 2, p50: 40, p90: 100, p99: 160 },
      ],
      source: { source_type: 'assumption', ref: 'mock://client-test/exposure' },
    },
    confidence: {
      level: 'Medium', score: 0.5, basis: 'ensemble_disagreement',
      source: { source_type: 'assumption', ref: 'mock://client-test/confidence' },
    },
    economics: {
      gpus_per_mw: 1000,
      lost_gpu_hours_per_year: { p50: 1800000, p90: 5400000, p99: 9000000 },
      annual_cost_usd: { p50: 1800000, p90: 5400000, p99: 9000000 },
      value_of_early_connection_usd: 10000000,
      breakeven_exposure_hours_per_year: 100,
      decision: 'close_call',
      source: { source_type: 'assumption', ref: 'mock://client-test/economics' },
    },
    tariff: {
      operator: 'SPP', service: 'Illustrative flexible service',
      curtailment_triggers: [{
        text: 'Illustrative trigger', observable: false,
        source: { source_type: 'assumption', ref: 'mock://client-test/tariff' },
      }],
    },
  }
}

function alter(path: string, value: unknown) {
  const response = fixture()
  const keys = path.split('.')
  let object: Record<string, unknown> = response as unknown as Record<string, unknown>
  for (const key of keys.slice(0, -1)) object = object[key] as Record<string, unknown>
  object[keys.at(-1)!] = value
  return response
}

function fetchResponse(body: unknown, status = 200) {
  return vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
}

afterEach(() => vi.unstubAllGlobals())

describe('validateEstimateResponse', () => {
  it('accepts canonical quantiles without p10 and keeps exact values and provenance', () => {
    const response = fixture()
    response.modeled_exposure.source = { source_type: 'model', ref: 'pipeline/simulate.py model_version=test_v1' }
    response.confidence.source = { source_type: 'model', ref: 'ensemble spread, n=20' }
    response.economics.source = { source_type: 'data', ref: 'test-fixture://sourced-economics' }
    response.tariff.curtailment_triggers[0].source = { source_type: 'clause', ref: 'test-fixture://tariff/page-14' }
    response.modeled_exposure.p50 = 30.123456789

    expect(validateEstimateResponse(response, request)).toBe(response)
    expect(response.modeled_exposure.p50).toBe(30.123456789)
    expect(response.modeled_exposure.by_year[0]).not.toHaveProperty('p10')
  })

  it('allows zero costs and a null break-even threshold without inventing a replacement', () => {
    const response = fixture()
    response.inputs_echo.flexibility_split = 0
    response.economics.lost_gpu_hours_per_year = { p50: 0, p90: 0, p99: 0 }
    response.economics.annual_cost_usd = { p50: 0, p90: 0, p99: 0 }
    response.economics.breakeven_exposure_hours_per_year = null
    expect(validateEstimateResponse(response).economics.breakeven_exposure_hours_per_year).toBeNull()
  })

  it.each(['inputs_echo', 'modeled_exposure', 'confidence', 'economics', 'tariff'])(
    'rejects a missing %s block', (block) => {
      const response = fixture() as unknown as Record<string, unknown>
      delete response[block]
      expect(() => validateEstimateResponse(response)).toThrow(EstimateClientError)
    },
  )

  it.each([
    ['modeled_exposure.p50', '30'],
    ['modeled_exposure.p90', Infinity],
    ['modeled_exposure.p99', NaN],
    ['modeled_exposure.worst_contiguous_outage_hours', -1],
    ['modeled_exposure.unit', 'curtailment hours'],
    ['modeled_exposure.by_year.0.p90', undefined],
    ['economics.gpus_per_mw', -1],
    ['economics.lost_gpu_hours_per_year.p50', NaN],
    ['economics.annual_cost_usd.p99', -1],
    ['economics.value_of_early_connection_usd', Infinity],
    ['economics.breakeven_exposure_hours_per_year', undefined],
    ['economics.breakeven_exposure_hours_per_year', -1],
    ['economics.decision', 'probably'],
    ['confidence.level', '95% certain'],
    ['confidence.score', 1.01],
    ['confidence.score', -0.1],
    ['confidence.score', NaN],
    ['confidence.basis', ''],
    ['tariff.operator', 'PJM'],
    ['tariff.service', ''],
    ['tariff.curtailment_triggers', null],
    ['tariff.curtailment_triggers.0.text', ''],
    ['tariff.curtailment_triggers.0.observable', 'false'],
  ])('rejects invalid %s = %s', (path, value) => {
    expect(() => validateEstimateResponse(alter(path, value))).toThrow(EstimateClientError)
  })

  it.each(['modeled_exposure', 'confidence', 'economics', 'tariff.curtailment_triggers.0'])(
    'rejects missing or invented provenance in %s', (block) => {
      for (const invalidSource of [undefined, {}, { source_type: 'model', ref: '   ' }, { source_type: 'inferred', ref: 'test' }]) {
        expect(() => validateEstimateResponse(alter(`${block}.source`, invalidSource))).toThrow(EstimateClientError)
      }
    },
  )

  it.each(['modeled_exposure', 'modeled_exposure.by_year.0', 'economics.lost_gpu_hours_per_year', 'economics.annual_cost_usd'])(
    'rejects reversed quantiles in %s', (block) => {
      expect(() => validateEstimateResponse(alter(`${block}.p50`, 10000000))).toThrow(/p50 <= p90 <= p99/)
      expect(() => validateEstimateResponse(alter(`${block}.p99`, 0))).toThrow(/p50 <= p90 <= p99/)
    },
  )

  it.each([
    [],
    [{ year: 1, p50: 20, p90: 80, p99: 140 }],
    [{ year: 1, p50: 20, p90: 80, p99: 140 }, { year: 1, p50: 40, p90: 100, p99: 160 }],
    [{ year: 2, p50: 40, p90: 100, p99: 160 }, { year: 1, p50: 20, p90: 80, p99: 140 }],
    [{ year: 1, p50: 20, p90: 80, p99: 140 }, { year: 3, p50: 40, p90: 100, p99: 160 }],
    [{ year: 0, p50: 20, p90: 80, p99: 140 }, { year: 2, p50: 40, p90: 100, p99: 160 }],
    [{ year: 1, p50: 20, p90: 80, p99: 140 }, { year: 1.5, p50: 40, p90: 100, p99: 160 }],
  ].map((rows) => ({ rows })))('rejects incomplete or invalid contract-year series %#', ({ rows }) => {
    expect(() => validateEstimateResponse(alter('modeled_exposure.by_year', rows))).toThrow(EstimateClientError)
  })

  it.each([
    ['location_id', 'SPP_OTHER_HUB'], ['load_mw', 200], ['term_years', 3],
    ['flexibility_split', 0.5], ['site_exposure', 0.8],
  ])('rejects a stale response echo for %s', (field, value) => {
    expect(() => validateEstimateResponse(alter(`inputs_echo.${field}`, value), request)).toThrow(/match the submitted request/)
  })
})

describe('postEstimate', () => {
  it('posts only the five canonical fields and forwards cancellation', async () => {
    const response = fixture()
    const fetchImpl = fetchResponse(response)
    const controller = new AbortController()
    const result = await postEstimate({ ...request, private_ui_field: 'not sent' } as EstimateRequest, {
      fetchImpl, signal: controller.signal,
    })
    expect(fetchImpl).toHaveBeenCalledExactlyOnceWith('http://127.0.0.1:8000/api/estimate', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(request), signal: controller.signal,
    })
    expect(result).toEqual(response)
  })

  it('snapshots submitted inputs while a response is pending', async () => {
    const inputs = { ...request }
    const pending = postEstimate(inputs, { fetchImpl: fetchResponse(fixture()) })
    inputs.site_exposure = 0.9
    expect((await pending).inputs_echo.site_exposure).toBe(0.3)
  })

  it('uses the browser fetch only when invoked', async () => {
    const fetchImpl = fetchResponse(fixture())
    vi.stubGlobal('fetch', fetchImpl)
    vi.resetModules()
    const client = await import('./client')
    expect(fetchImpl).not.toHaveBeenCalled()
    await client.postEstimate(request)
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it.each([
    ['load_mw', NaN], ['load_mw', Infinity], ['load_mw', 0], ['load_mw', -1], ['load_mw', '100'],
    ['term_years', 0], ['term_years', 1.5], ['term_years', Infinity], ['term_years', 8],
    ['flexibility_split', -0.1], ['flexibility_split', 1.1], ['flexibility_split', NaN],
    ['site_exposure', -0.1], ['site_exposure', 1.1], ['site_exposure', NaN], ['location_id', ''],
  ])('rejects invalid request %s = %s before contacting the API', async (field, value) => {
    const fetchImpl = fetchResponse(fixture())
    await expect(postEstimate({ ...request, [field]: value }, { fetchImpl })).rejects.toMatchObject({
      code: 'invalid_request', path: field,
    })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('rejects malformed server data instead of returning a partly usable result', async () => {
    await expect(postEstimate(request, { fetchImpl: fetchResponse(alter('confidence.source', undefined)) }))
      .rejects.toMatchObject({ code: 'invalid_response', path: 'confidence.source' })
  })

  it('rejects a successful HTTP response whose inputs belong to another request', async () => {
    await expect(postEstimate(request, { fetchImpl: fetchResponse(alter('inputs_echo.site_exposure', 0.9)) }))
      .rejects.toMatchObject({ code: 'invalid_response', path: 'inputs_echo.site_exposure' })
  })

  it('reports a failed HTTP status without exposing the server body', async () => {
    await expect(postEstimate(request, { fetchImpl: fetchResponse({ detail: 'internal detail' }, 503) }))
      .rejects.toMatchObject({ code: 'http_error', status: 503, message: 'Estimate request failed (HTTP 503).' })
  })

  it('reports invalid JSON as a typed response error', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response('<html>not JSON</html>'))
    await expect(postEstimate(request, { fetchImpl })).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('propagates native cancellation without substituting mock data', async () => {
    const controller = new AbortController()
    const aborted = new DOMException('Cancelled', 'AbortError')
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(aborted), { once: true })
    }))
    const pending = postEstimate(request, { fetchImpl, signal: controller.signal })
    controller.abort()
    await expect(pending).rejects.toBe(aborted)
  })

  it('preserves a network failure for the calling UI to handle', async () => {
    const offline = new TypeError('Failed to fetch')
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(offline)
    await expect(postEstimate(request, { fetchImpl })).rejects.toBe(offline)
  })

  it('preserves cancellation while decoding the response body', async () => {
    const aborted = new DOMException('Cancelled', 'AbortError')
    const response = new Response('{}')
    vi.spyOn(response, 'json').mockRejectedValue(aborted)
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response)
    await expect(postEstimate(request, { fetchImpl })).rejects.toBe(aborted)
  })
})
