import { describe, expect, it } from 'vitest'
import {
  adaptEstimateResponse, createMockEstimate, defaultInputs, deriveScenario,
  mockResponse, toEstimateRequest,
} from './index'
import responseJson from './mock-response.json'

describe('canonical offline estimate contract', () => {
  it('matches the complete canonical JSON example supplied for the backend', () => {
    expect(createMockEstimate(toEstimateRequest(defaultInputs))).toEqual(responseJson)
  })

  it('sends exactly the five API fields, converting percent to a fraction', () => {
    const request = toEstimateRequest({ ...defaultInputs, flexibility_percent: 35 })
    expect(request).toEqual({
      location_id: defaultInputs.location_id,
      load_mw: 100,
      term_years: 7,
      flexibility_split: 0.35,
      site_exposure: 0.4,
    })
    const response = createMockEstimate(request)
    expect(Object.keys(response)).toEqual(['inputs_echo', 'modeled_exposure', 'confidence', 'economics', 'tariff'])
    expect(Object.keys(response.modeled_exposure.by_year[0])).toEqual(['year', 'p50', 'p90', 'p99'])
  })

  it('has zero modeled exposure and zero modeled loss at zero site exposure', () => {
    const result = deriveScenario({ ...defaultInputs, site_exposure: 0 })
    expect(Object.values(result.annual_exposure).map((item) => item.value)).toEqual([0, 0, 0])
    expect(result.worst_contiguous_exposure.value).toBe(0)
    expect(result.economics.annual_lost_gpu_hours.value).toBe(0)
    expect(result.economics.term_loss_usd.value).toBe(0)
    expect(result.economics.net_value_usd.value).toBe(result.economics.early_access_value_usd.value)
    // The canonical zero-exposure response cannot recover a nonzero baseline.
    expect(result.economics.break_even_site_exposure.value).toBeNull()
  })

  it.each([0, 0.4, 1])('scales each supplied yearly quantile exactly once at site exposure %s', (siteExposure) => {
    const quantiles = ['p50', 'p90', 'p99'] as const
    mockResponse.locations.forEach((location) => {
      const result = deriveScenario({
        ...defaultInputs, location_id: location.id, contract_years: 7, site_exposure: siteExposure,
      })
      expect(result.annual_series).toHaveLength(7)
      result.annual_series.forEach((row, index) => {
        const baseline = location.annual_series[index]
        expect(row.year.value).toEqual(baseline.year.value)
        const values = quantiles.map((quantile) => row[quantile].value)
        expect(values).toEqual([...values].sort((left, right) => left - right))
        quantiles.forEach((quantile) => {
          const value = row[quantile]
          expect(value.value).toBeCloseTo(baseline[quantile].value * siteExposure, 12)
          expect(value.source_type).toBe('assumption')
          const ref = new URL(value.ref)
          expect(ref.protocol).toBe('mock:')
          expect(ref.searchParams.get('location_id')).toBe(location.id)
          expect(ref.searchParams.get('site_exposure')).toBe(String(siteExposure))
          expect(ref.hash).toBe(`#modeled_exposure/by_year/${index}/${quantile}`)
        })
      })
      quantiles.forEach((quantile) => {
        const marginalAverage = result.annual_series.reduce((sum, row) => sum + row[quantile].value, 0) / 7
        expect(result.annual_exposure[quantile].value).toBeCloseTo(marginalAverage, 12)
      })
    })
  })

  it('converts all exposure quantiles into GPU-hours and dollars using explicit mock assumptions', () => {
    const result = deriveScenario({
      ...defaultInputs, contract_years: 1, load_mw: 10, flexibility_percent: 50,
      site_exposure: 0.5, gpu_per_mw: 100, gpu_hour_value_usd: 2,
      firm_wait_years: 3, early_margin_usd_per_mw_year: 1_000,
    })
    expect(result.annual_exposure.p50.value).toBe(100)
    expect(result.economics.interruptible_mw.value).toBe(5)
    expect(result.economics.annual_lost_gpu_hours.value).toBe(50_000)
    expect(result.economics.annual_loss_usd.value).toBe(100_000)
    expect(result.economics.annual_loss_by_quantile.p90.value).toBe(168_000)
    expect(result.economics.annual_lost_gpu_hours_by_quantile.p99.value).toBe(127_500)
    // A single-year comparison does not credit three years of earlier operation.
    expect(result.economics.early_access_value_usd.value).toBe(10_000)
    expect(result.economics.net_value_usd.value).toBe(-90_000)
    expect(result.economics.break_even_exposure_hours.value).toBe(10)
    expect(result.economics.break_even_site_exposure.value).toBe(0.05)
  })

  it('uses term p50 and p90 comparison proxies to produce all three decisions', () => {
    const base = deriveScenario(defaultInputs)
    expect(base.economics.break_even_exposure_hours.value).toBeCloseTo(126_800_000 / (7 * 103_500), 12)
    expect(base.decision).toBe('worth it')
    expect(deriveScenario({ ...defaultInputs, site_exposure: 0.55 }).decision).toBe('close call')
    expect(deriveScenario({ ...defaultInputs, site_exposure: base.economics.break_even_site_exposure.value! }).decision).toBe('close call')
    expect(deriveScenario({ ...defaultInputs, site_exposure: 0.9 }).decision).toBe('not worth it')
    expect(deriveScenario({ ...defaultInputs, load_mw: 0 }).decision).toBe('close call')
  })

  it.each(['flexibility_percent', 'gpu_per_mw', 'gpu_hour_value_usd'] as const)('returns JSON-safe nullable break-even when %s makes interruption cost zero', (field) => {
    const result = deriveScenario({ ...defaultInputs, [field]: 0 })
    expect(result.economics.term_loss_usd.value).toBe(0)
    expect(result.economics.break_even_exposure_hours.value).toBeNull()
    expect(result.economics.break_even_site_exposure.value).toBeNull()
    expect(result.canonical_response.economics.breakeven_exposure_hours_per_year).toBeNull()
    expect(JSON.parse(JSON.stringify(result.canonical_response))).toEqual(result.canonical_response)
  })

  it('preserves backend-scaled numbers and model provenance without reapplying the slider', () => {
    const response = createMockEstimate(toEstimateRequest(defaultInputs))
    response.modeled_exposure.p50 = 37
    response.modeled_exposure.by_year[0].p50 = 23
    response.modeled_exposure.source = { source_type: 'model', ref: 'pipeline/simulate.py model_version=fixture-for-adapter-test' }
    response.confidence.source = { source_type: 'model', ref: 'confidence-fixture-for-adapter-test' }
    response.economics.annual_cost_usd.p99 = 90_000
    const result = adaptEstimateResponse(response)
    expect(result.annual_exposure.p50.value).toBe(37)
    expect(result.annual_series[0].p50.value).toBe(23)
    expect(result.annual_exposure.p50.source_type).toBe('model')
    expect(result.annual_exposure.p50.ref).toContain(response.modeled_exposure.source.ref)
    expect(result.confidence.score.source_type).toBe('model')
    expect(result.economics.annual_loss_by_quantile.p99.value).toBe(90_000)
    expect(result.canonical_response).toBe(response)
  })

  it('keeps confidence explicitly illustrative and independent of the site assumption', () => {
    const low = deriveScenario({ ...defaultInputs, site_exposure: 0 })
    const high = deriveScenario({ ...defaultInputs, site_exposure: 1 })
    expect(low.confidence).toEqual(high.confidence)
    expect(low.confidence.level).toBe('Medium')
    expect(low.confidence.score.value).toBe(0.5)
    expect(low.confidence.score.source_type).toBe('assumption')
    expect(low.confidence.basis).toBe('illustrative_fixture_not_ensemble_inference')
  })

  it('is deterministic and respects each supported contract horizon', () => {
    for (let contract_years = 1; contract_years <= 7; contract_years++) {
      const inputs = { ...defaultInputs, contract_years }
      const result = deriveScenario(inputs)
      expect(result).toEqual(deriveScenario(inputs))
      expect(result.annual_series).toHaveLength(contract_years)
      expect(result.annual_series.at(-1)?.year.value).toBe(contract_years)
    }
  })

  it('retains mock provenance for every canonical result block and never fabricates a clause citation', () => {
    const response = createMockEstimate(toEstimateRequest(defaultInputs))
    // Exposure/confidence/tariff remain illustrative fixture output; economics is
    // partially sourced (docs/ASSUMPTIONS.md status "mixed") but stays mock:// until
    // that file is fully "sourced" - checked separately below with its own prefix.
    for (const source of [response.modeled_exposure.source, response.confidence.source, ...response.tariff.curtailment_triggers.map((trigger) => trigger.source)]) {
      expect(source.source_type).toBe('assumption')
      expect(source.ref).toMatch(/^mock:\/\/illustrative\//)
    }
    expect(response.economics.source.source_type).toBe('assumption')
    expect(response.economics.source.ref).toMatch(/^mock:\/\/economics-placeholder\/docs\/ASSUMPTIONS\.md\?/)
    expect(response.tariff.service).toContain('mock; not extracted')
    expect(response.tariff.curtailment_triggers[0].text).toContain('no tariff clause has been extracted')
    expect(JSON.stringify(response)).not.toContain('FERC')
  })

  it('keeps numeric view values inside provenance wrappers, using block inheritance from the response', () => {
    const result = deriveScenario(defaultInputs)
    const { canonical_response: canonicalResponse, tariff, ...view } = result
    expect(canonicalResponse).toBeDefined()
    expect(tariff).toBeDefined()
    const verify = (value: unknown): void => {
      if (Array.isArray(value)) value.forEach(verify)
      else if (value !== null && typeof value === 'object') {
        const object = value as Record<string, unknown>
        if ('value' in object) {
          expect(object.source_type).toBe('assumption')
          expect(object.ref).toMatch(/^(mock:\/\/illustrative\/|mock:\/\/economics-placeholder\/|user:\/\/estimate\/|docs\/ASSUMPTIONS\.md)/)
          if (typeof object.value === 'number') expect(Number.isFinite(object.value)).toBe(true)
        } else Object.values(object).forEach(verify)
      } else expect(typeof value).not.toBe('number')
    }
    verify(mockResponse)
    verify(view)
  })

  it('rejects invalid inputs before producing misleading numbers', () => {
    expect(() => deriveScenario({ ...defaultInputs, site_exposure: 1.1 })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, flexibility_percent: -1 })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, contract_years: 8 })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, contract_years: 0 })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, load_mw: Number.NaN })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, gpu_per_mw: Number.POSITIVE_INFINITY })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, location_id: 'not-spp' })).toThrow(RangeError)
  })
})
