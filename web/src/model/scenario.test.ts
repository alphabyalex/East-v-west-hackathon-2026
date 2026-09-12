import { describe, expect, it } from 'vitest'
import { defaultInputs, deriveScenario, mockResponse } from './index'
import responseJson from './mock-response.json'

describe('illustrative scenario arithmetic', () => {
  it('matches the complete JSON example supplied for the backend contract', () => {
    expect(mockResponse).toEqual(responseJson)
  })

  it('has zero modeled exposure and zero modeled loss at zero site exposure', () => {
    const result = deriveScenario({ ...defaultInputs, site_exposure: 0 })
    expect(Object.values(result.annual_exposure).map((item) => item.value)).toEqual([0, 0, 0])
    expect(result.economics.annual_lost_gpu_hours.value).toBe(0)
    expect(result.economics.term_loss_usd.value).toBe(0)
    expect(result.economics.net_value_usd.value).toBe(result.economics.early_access_value_usd.value)
  })

  it('preserves quantile ordering and scales all exposure quantiles linearly', () => {
    const half = deriveScenario({ ...defaultInputs, site_exposure: 0.5 })
    const full = deriveScenario({ ...defaultInputs, site_exposure: 1 })
    half.annual_series.forEach((row, index) => {
      expect(row.p10.value).toBeLessThanOrEqual(row.p50.value)
      expect(row.p50.value).toBeLessThanOrEqual(row.p90.value)
      expect(row.p50.value * 2).toBe(full.annual_series[index].p50.value)
      expect(row.p90.value * 2).toBe(full.annual_series[index].p90.value)
    })
    expect(half.annual_exposure.p90.value).toBeLessThanOrEqual(half.annual_exposure.p99.value)
    expect(half.economics.term_loss_usd.value * 2).toBe(full.economics.term_loss_usd.value)
  })

  it('converts interrupted load into GPU-hours and dollars using explicit assumptions', () => {
    const result = deriveScenario({
      ...defaultInputs,
      contract_years: 1,
      load_mw: 10,
      flexibility_percent: 50,
      site_exposure: 0.5,
      gpu_per_mw: 100,
      gpu_hour_value_usd: 2,
      firm_wait_years: 3,
      early_margin_usd_per_mw_year: 1_000,
    })
    expect(result.annual_exposure.p50.value).toBe(100)
    expect(result.economics.interruptible_mw.value).toBe(5)
    expect(result.economics.annual_lost_gpu_hours.value).toBe(50_000)
    expect(result.economics.annual_loss_usd.value).toBe(100_000)
    // A single-year comparison does not credit three years of earlier operation.
    expect(result.economics.early_access_value_usd.value).toBe(10_000)
    expect(result.economics.net_value_usd.value).toBe(-90_000)
    expect(result.economics.break_even_exposure_hours.value).toBe(10)
    expect(result.economics.break_even_site_exposure.value).toBe(0.05)
  })

  it('changes the decision at the calculated crossover with an explicit close-call zone', () => {
    const base = deriveScenario(defaultInputs)
    const crossover = base.economics.break_even_site_exposure.value!
    expect(crossover).toBeGreaterThan(0.5)
    expect(crossover).toBeLessThan(0.7)
    expect(base.decision).toBe('worth it')
    expect(deriveScenario({ ...defaultInputs, site_exposure: crossover }).decision).toBe('close call')
    expect(deriveScenario({ ...defaultInputs, site_exposure: 0.9 }).decision).toBe('not worth it')
  })

  it('does not create Infinity or NaN when modeled interruption cost is zero', () => {
    const result = deriveScenario({ ...defaultInputs, flexibility_percent: 0 })
    expect(result.economics.term_loss_usd.value).toBe(0)
    expect(result.economics.break_even_exposure_hours.value).toBeNull()
    expect(result.economics.break_even_site_exposure.value).toBeNull()
  })

  it('is deterministic and follows the selected contract horizon', () => {
    const inputs = { ...defaultInputs, contract_years: 20 }
    const result = deriveScenario(inputs)
    expect(result).toEqual(deriveScenario(inputs))
    expect(result.annual_series).toHaveLength(20)
    expect(result.annual_series.at(-1)?.year.value).toBe(20)
  })

  it('keeps every numeric fixture and derived value inside honest provenance', () => {
    const verify = (value: unknown): void => {
      if (Array.isArray(value)) {
        value.forEach(verify)
      } else if (value !== null && typeof value === 'object') {
        const object = value as Record<string, unknown>
        if ('value' in object) {
          expect(object.source_type).toBe('assumption')
          expect(object.ref).toMatch(/^mock:\/\/illustrative\//)
          if (typeof object.value === 'number') expect(Number.isFinite(object.value)).toBe(true)
        } else {
          Object.values(object).forEach(verify)
        }
      } else {
        expect(typeof value).not.toBe('number')
      }
    }
    verify(mockResponse)
    verify(deriveScenario(defaultInputs))
  })

  it('rejects invalid inputs before producing misleading numbers', () => {
    expect(() => deriveScenario({ ...defaultInputs, site_exposure: 1.1 })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, contract_years: 21 })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, load_mw: Number.NaN })).toThrow(RangeError)
    expect(() => deriveScenario({ ...defaultInputs, location_id: 'not-spp' })).toThrow(RangeError)
  })
})
