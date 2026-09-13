import { describe, expect, it } from 'vitest'
import snapshot from '../model/economics-assumptions.json'
import { createMockEstimate, defaultInputs, toEstimateRequest } from '../model'
import { validateEconomicConsistency, validateEconomicsAssumptions } from './assumptions'

describe('economics metadata boundary', () => {
  it('accepts the complete mixed-provenance bundled snapshot', () => {
    expect(validateEconomicsAssumptions(snapshot)).toBe(snapshot)
  })

  it.each([
    ['status', 'sourced'],
    ['early_margin_usd_per_mw_year.source_type', 'data'],
    ['early_margin_usd_per_mw_year.source_url', 'https://example.com/fake'],
    ['gpu_rental_price_usd_per_hour.source_url', null],
    ['gpu_rental_price_usd_per_hour.source_url', 'https://user:password@example.com'],
    ['gpu_rental_price_usd_per_hour.retrieved_on', '2026-02-30'],
    ['gpus_per_mw.unit', 'GPU/IT-MW'],
    ['gpus_per_mw.value', 0],
    ['gpu_rental_price_usd_per_hour.value', Number.POSITIVE_INFINITY],
    ['unknown', true],
    ['close_call_fraction.unexpected', true],
  ])('rejects inconsistent metadata %s', (path, value) => {
    const input = structuredClone(snapshot) as unknown as Record<string, unknown>
    const [key, field] = path.split('.')
    if (field) (input[key] as Record<string, unknown>)[field] = value
    else input[key] = value
    expect(() => validateEconomicsAssumptions(input)).toThrow()
  })

  it('checks every economic result against the metadata without rescaling exposure', () => {
    const response = createMockEstimate(toEstimateRequest(defaultInputs))
    const assumptions = validateEconomicsAssumptions(snapshot)
    expect(() => validateEconomicConsistency(response, assumptions)).not.toThrow()
    for (const field of ['p50', 'p90', 'p99'] as const) {
      const broken = structuredClone(response)
      broken.economics.annual_cost_usd[field] *= 1.01
      expect(() => validateEconomicConsistency(broken, assumptions)).toThrow()
    }
    const broken = structuredClone(response)
    broken.economics.decision = 'not_worth_it'
    expect(() => validateEconomicConsistency(broken, assumptions)).toThrow()
  })

  it('keeps the nullable break-even contract when interruption cost is zero', () => {
    const response = createMockEstimate({ ...toEstimateRequest(defaultInputs), flexibility_split: 0 })
    expect(() => validateEconomicConsistency(response, validateEconomicsAssumptions(snapshot))).not.toThrow()
    response.economics.breakeven_exposure_hours_per_year = 0
    expect(() => validateEconomicConsistency(response, validateEconomicsAssumptions(snapshot))).toThrow()
  })
})
