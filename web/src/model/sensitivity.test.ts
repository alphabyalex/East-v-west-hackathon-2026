import { describe, expect, it } from 'vitest'
import { validateEconomicsAssumptions } from '../api/assumptions'
import { buildEstimateSensitivity } from '../api/sensitivity'
import economicSnapshot from './economics-assumptions.json'
import { createMockEstimate, toEstimateRequest } from './estimate'
import { defaultInputs } from './fixture'
import { buildSensitivity, type SensitivityKey, type SensitivityResult } from './sensitivity'
import type { ScenarioInputs } from './types'

function scenario(overrides: Partial<ScenarioInputs> = {}) {
  const inputs = { ...defaultInputs, ...overrides }
  const request = toEstimateRequest(inputs)
  const response = createMockEstimate(request, inputs)
  const exposureAtOne = createMockEstimate({ ...request, site_exposure: 1 }, inputs)
  const assumptions = validateEconomicsAssumptions(structuredClone(economicSnapshot))
  const calculate = () => buildSensitivity(response, exposureAtOne, inputs, assumptions)
  return { inputs, response, exposureAtOne, assumptions, calculate }
}

function modeled(result: SensitivityResult, key: SensitivityKey) {
  const row = result.rows.find(item => item.key === key)!
  if (row.status !== 'modeled') throw new Error(`Expected ${key} to be modeled`)
  return row
}

describe('one-at-a-time canonical economics sensitivity', () => {
  it.each([1, 50, 500, 10000])('recomputes the VPP baseline and every endpoint with %s solar homes', async homes => {
    const { inputs, response, calculate, assumptions } = scenario({ vpp_solar_homes: homes })
    const result = calculate()
    expect(result.baseline.annual_cost_usd.p50.value).toBe(response.economics.annual_cost_usd.p50)
    expect(result.baseline.breakeven_hours.value).toBe(response.economics.breakeven_exposure_hours_per_year)
    for (const row of result.rows) {
      if (row.status !== 'modeled') continue
      for (const endpoint of [row.low, row.high]) {
        const changed = { ...inputs,
          ...(row.key === 'gpu_rental_price' ? { gpu_hour_value_usd: endpoint.input.value } : {}),
          ...(row.key === 'flexibility_split' ? { flexibility_percent: endpoint.input.value * 100 } : {}),
          ...(row.key === 'site_exposure' ? { site_exposure: endpoint.input.value } : {}),
        }
        const expected = createMockEstimate(toEstimateRequest(changed), changed).economics
        for (const key of ['p50', 'p90', 'p99'] as const) {
          expect(endpoint.snapshot.annual_cost_usd[key].value).toBeCloseTo(expected.annual_cost_usd[key], 6)
        }
        expect(endpoint.snapshot.decision).toBe(expected.decision)
        expect(endpoint.snapshot.breakeven_hours.value).toBe(expected.breakeven_exposure_hours_per_year)
        expect(endpoint.source.ref).toContain('VPP homes and assumptions held fixed')
      }
    }
    expect((await buildEstimateSensitivity(response, assumptions)).baseline.annual_cost_usd.p50.value)
      .toBe(response.economics.annual_cost_usd.p50)
  })

  it('preserves baseline economics and the independent rounded early-margin input exactly', () => {
    const { response, calculate } = scenario()
    const { baseline } = calculate()
    expect(baseline.early_value_usd.value).toBe(response.economics.value_of_early_connection_usd)
    expect(baseline.early_value_usd.value).toBe(126_800_000)
    expect(baseline.decision).toBe(response.economics.decision)
    expect(baseline.breakeven_hours.value).toBe(response.economics.breakeven_exposure_hours_per_year)
    for (const key of ['p50', 'p90', 'p99'] as const) {
      expect(baseline.annual_cost_usd[key].value).toBe(response.economics.annual_cost_usd[key])
      expect(baseline.net_value_usd[key].value).toBe(response.economics.value_of_early_connection_usd
        - response.economics.annual_cost_usd[key] * response.inputs_echo.term_years)
    }
  })

  it('varies rental cost only, with the analytic cost slope and fixed early benefit', () => {
    const { response, inputs, assumptions, calculate } = scenario({ contract_years: 1 })
    const row = modeled(calculate(), 'gpu_rental_price')
    const gpuHours = response.economics.lost_gpu_hours_per_year.p50
    expect(row.low.input.value).toBe(assumptions.gpu_rental_price_usd_per_hour.low)
    expect(row.high.input.value).toBe(assumptions.gpu_rental_price_usd_per_hour.high)
    for (const endpoint of [row.low, row.high]) {
      expect(endpoint.delta_value_usd.value).toBeCloseTo(-gpuHours * (endpoint.input.value - inputs.gpu_hour_value_usd), 8)
      expect(endpoint.snapshot.early_value_usd.value).toBe(response.economics.value_of_early_connection_usd)
      expect(endpoint.snapshot.annual_cost_usd.p99.value).toBeCloseTo(response.economics.lost_gpu_hours_per_year.p99 * endpoint.input.value, 8)
    }
    expect(row.low.delta_value_usd.value).toBeGreaterThan(0)
    expect(row.high.delta_value_usd.value).toBeLessThan(0)
  })

  it('varies flexibility independently, restoring cost from exposure even when current flexibility is zero', () => {
    const { calculate, exposureAtOne, response, inputs } = scenario({ flexibility_percent: 0, contract_years: 1 })
    const row = modeled(calculate(), 'flexibility_split')
    expect(response.economics.annual_cost_usd.p50).toBe(0)
    expect(row.low.snapshot.annual_cost_usd.p50.value).toBe(0)
    expect(row.high.snapshot.annual_cost_usd.p50.value).toBe(exposureAtOne.modeled_exposure.p50
      * inputs.site_exposure * inputs.load_mw * inputs.gpu_per_mw * inputs.gpu_hour_value_usd)
    expect(row.high.snapshot.early_value_usd.value).toBe(row.low.snapshot.early_value_usd.value)
    expect(row.low.snapshot.breakeven_hours.value).toBeNull()
    expect(row.low.snapshot.breakeven_note).toContain('zero')
  })

  it('recovers the complete site-factor span when the active site factor is zero', () => {
    const { calculate, exposureAtOne } = scenario({ site_exposure: 0 })
    const row = modeled(calculate(), 'site_exposure')
    expect(row.low.delta_value_usd.value).toBe(0)
    expect(row.low.snapshot.annual_cost_usd.p50.value).toBe(0)
    expect(row.high.snapshot.annual_cost_usd.p50.value).toBeCloseTo(exposureAtOne.economics.annual_cost_usd.p50, 6)
    expect(row.high.delta_value_usd.value).toBeCloseTo(-exposureAtOne.economics.annual_cost_usd.p50 * exposureAtOne.inputs_echo.term_years, 5)
    // The hour threshold is independent of how many exposure hours the site maps to.
    expect(row.low.snapshot.breakeven_hours.value).toBe(row.high.snapshot.breakeven_hours.value)
  })

  it('reconstructs rental sensitivity at a zero baseline rental price without dividing by it', () => {
    const { calculate } = scenario({ gpu_hour_value_usd: 0 })
    const result = calculate()
    expect(result.baseline.breakeven_hours.value).toBeNull()
    const row = modeled(result, 'gpu_rental_price')
    expect(row.low.snapshot.annual_cost_usd.p50.value).toBeGreaterThan(0)
    expect(row.high.snapshot.annual_cost_usd.p50.value).toBeGreaterThan(row.low.snapshot.annual_cost_usd.p50.value)
    expect(row.low.delta_value_usd.value).toBeLessThan(0)
  })

  it('marks electricity and utilization not modeled without inventing zero effects or decisions', () => {
    const result = scenario().calculate()
    const unavailable = result.rows.filter(row => row.status === 'not_modeled')
    expect(unavailable.map(row => row.key)).toEqual(['electricity_price', 'utilization'])
    for (const row of unavailable) {
      expect(row.reason).toBeTruthy()
      expect(row.swing_usd).toBeNull()
      for (const endpoint of [row.low, row.high]) {
        expect(endpoint.snapshot).toBeNull()
        expect(endpoint.delta_value_usd).toBeNull()
        expect(endpoint.input.ref).toContain('docs/ASSUMPTIONS.md')
        expect(endpoint.source.source_type).toBe('assumption')
      }
    }
    expect(unavailable[0].low.input.value).toBe(76)
    expect(unavailable[0].high.input.value).toBe(86)
    expect(unavailable[1].baseline_input.value).toBe(0.7)
    expect(unavailable[1].low.input.value).toBe(0.6)
    expect(unavailable[1].high.input.value).toBe(0.85)
  })

  it('sorts modeled bars by absolute endpoint swing, then lists unmodeled factors separately', () => {
    const result = scenario().calculate()
    expect(result.rows.map(row => row.key)).toEqual([
      'site_exposure', 'flexibility_split', 'gpu_rental_price', 'electricity_price', 'utilization',
    ])
    const rows = result.rows.filter(row => row.status === 'modeled')
    expect(rows.map(row => row.swing_usd.value)).toEqual(rows.map(row => row.swing_usd.value).sort((a, b) => b - a))
    rows.forEach(row => expect(row.swing_usd.value).toBe(Math.abs(row.high.delta_value_usd.value - row.low.delta_value_usd.value)))
    const zeroRows = scenario({ gpu_per_mw: 0 }).calculate().rows.filter(row => row.status === 'modeled')
    expect(zeroRows.map(row => row.key)).toEqual(['flexibility_split', 'gpu_rental_price', 'site_exposure'])
  })

  it('allows both endpoints on one side of the baseline when the user exceeds the recorded range', () => {
    const row = modeled(scenario({ gpu_hour_value_usd: 10 }).calculate(), 'gpu_rental_price')
    expect(row.low.input.value).toBe(1.49)
    expect(row.high.input.value).toBe(6.16)
    expect(row.baseline_input.value).toBe(10)
    expect(row.baseline_input.ref).toContain('user://')
    expect(row.low.delta_value_usd.value).toBeGreaterThan(0)
    expect(row.high.delta_value_usd.value).toBeGreaterThan(0)
  })

  it('reproduces the canonical three-way decision at every modeled endpoint', () => {
    const { inputs, calculate } = scenario({ site_exposure: 0.55 })
    const result = calculate()
    expect(result.baseline.decision).toBe('close_call')
    for (const row of result.rows) {
      if (row.status !== 'modeled') continue
      for (const endpoint of [row.low, row.high]) {
        const changed = {
          ...inputs,
          ...(row.key === 'gpu_rental_price' ? { gpu_hour_value_usd: endpoint.input.value } : {}),
          ...(row.key === 'flexibility_split' ? { flexibility_percent: endpoint.input.value * 100 } : {}),
          ...(row.key === 'site_exposure' ? { site_exposure: endpoint.input.value } : {}),
        }
        const expected = createMockEstimate(toEstimateRequest(changed), changed)
        expect(endpoint.snapshot.decision).toBe(expected.economics.decision)
        expect(endpoint.snapshot.annual_cost_usd.p90.value).toBeCloseTo(expected.economics.annual_cost_usd.p90, 7)
      }
    }
    const site = modeled(result, 'site_exposure')
    expect(site.low.snapshot.decision).toBe('worth_it')
    expect(site.high.snapshot.decision).toBe('not_worth_it')
  })

  it('does not equate positive p50 value with worth_it when p90 crosses the decision threshold', () => {
    const result = scenario({ site_exposure: 0.55 }).calculate()
    expect(result.baseline.net_value_usd.p50.value).toBeGreaterThan(0)
    expect(result.baseline.net_value_usd.p90.value).toBeLessThan(0)
    expect(result.baseline.decision).toBe('close_call')
  })

  it('preserves strict threshold equality with the canonical multiplication grouping', async () => {
    const data = scenario({
      contract_years: 1, site_exposure: 0.03, load_mw: 123.45,
      flexibility_percent: 37, early_margin_usd_per_mw_year: 1811.4142857142856,
    })
    const changed = { ...data.inputs, gpu_hour_value_usd: 1.49 }
    const canonical = createMockEstimate(toEstimateRequest(changed), changed)
    expect(canonical.economics.annual_cost_usd.p50).toBe(234800.04825)
    expect(canonical.economics.annual_cost_usd.p50).toBe(canonical.economics.value_of_early_connection_usd * 1.05)
    expect(canonical.economics.decision).toBe('close_call')
    const direct = modeled(data.calculate(), 'gpu_rental_price').low.snapshot
    expect(direct.annual_cost_usd.p50.value).toBe(canonical.economics.annual_cost_usd.p50)
    expect(direct.breakeven_hours.value).toBe(canonical.economics.breakeven_exposure_hours_per_year)
    expect(direct.decision).toBe(canonical.economics.decision)
    // Exercise the transport's nonzero inverse baseline as well as a supplied
    // site=1 response; neither may perturb an unchanged exposure input.
    data.assumptions.early_margin_usd_per_mw_year.value = data.inputs.early_margin_usd_per_mw_year
    const recovered = await buildEstimateSensitivity(data.response, data.assumptions)
    const inverse = modeled(recovered, 'gpu_rental_price').low.snapshot
    expect(inverse.annual_cost_usd.p50.value).toBe(canonical.economics.annual_cost_usd.p50)
    expect(inverse.decision).toBe(canonical.economics.decision)
  })

  it('handles zero early benefit and zero-cost equality using the existing strict thresholds', () => {
    const { calculate } = scenario({ early_margin_usd_per_mw_year: 0, site_exposure: 0 })
    const result = calculate()
    expect(result.baseline.decision).toBe('close_call')
    expect(result.baseline.breakeven_hours.value).toBe(0)
    const site = modeled(result, 'site_exposure')
    expect(site.high.snapshot.decision).toBe('not_worth_it')
    expect(site.high.snapshot.net_value_usd.p50.value).toBeLessThan(0)
  })

  it('retains placeholder provenance even when exposure is model-sourced, and wraps every displayed number', () => {
    const data = scenario()
    data.response.modeled_exposure.source = { source_type: 'model', ref: 'pipeline/simulate.py model_version=verified-test?site_exposure=0.4' }
    data.exposureAtOne.modeled_exposure.source = { source_type: 'model', ref: 'pipeline/simulate.py model_version=verified-test?site_exposure=1' }
    const result = data.calculate()
    expect(result.source.ref).toMatch(/^mock:/)
    expect(result.source.ref).toContain('model_version=verified-test')
    expect(result.source.ref).toContain(data.response.economics.source.ref)
    expect(result.source.ref).toContain(data.assumptions.close_call_fraction.ref)
    const inspect = (value: unknown): void => {
      if (!value || typeof value !== 'object') return
      const record = value as Record<string, unknown>
      if ('value' in record && (typeof record.value === 'number' || record.value === null)) {
        expect(record.source_type).toBe('assumption')
        expect(typeof record.ref).toBe('string')
        expect(String(record.ref).length).toBeGreaterThan(0)
      }
      Object.values(record).forEach(inspect)
    }
    inspect(result)
    expect(JSON.parse(JSON.stringify(result))).toEqual(result)
  })

  it('does not mutate either response, scenario controls, or assumption metadata', () => {
    const data = scenario()
    const before = JSON.stringify([data.response, data.exposureAtOne, data.inputs, data.assumptions])
    const first = data.calculate()
    expect(JSON.stringify([data.response, data.exposureAtOne, data.inputs, data.assumptions])).toBe(before)
    expect(data.calculate()).toEqual(first)
  })

  it.each(['location', 'site', 'provenance', 'horizon', 'exposure', 'rental', 'tolerance', 'confidence', 'duration'] as const)(
    'rejects an inconsistent %s baseline instead of drawing invented comparisons', kind => {
      const data = scenario()
      if (kind === 'location') data.exposureAtOne.inputs_echo.location_id = 'another-location'
      if (kind === 'site') data.exposureAtOne.inputs_echo.site_exposure = 0
      if (kind === 'provenance') data.exposureAtOne.modeled_exposure.source.ref = 'another-model-version'
      if (kind === 'horizon') data.exposureAtOne.modeled_exposure.by_year.pop()
      if (kind === 'exposure') data.exposureAtOne.modeled_exposure.p50 += 50
      if (kind === 'rental') data.inputs.gpu_hour_value_usd = 9
      if (kind === 'tolerance') data.assumptions.close_call_fraction.value = 1
      if (kind === 'confidence') data.exposureAtOne.confidence.score = 0.7
      if (kind === 'duration') data.exposureAtOne.modeled_exposure.worst_contiguous_outage_hours += 20
      expect(data.calculate).toThrow(RangeError)
    },
  )

  it('rejects model version changes even when version metadata is inside the source query string', () => {
    const data = scenario()
    data.response.modeled_exposure.source.ref = 'model://test?model_version=first&site_exposure=0.4'
    data.exposureAtOne.modeled_exposure.source.ref = 'model://test?model_version=second&site_exposure=1'
    expect(data.calculate).toThrow('matching exposure provenance')
  })

  it('does not emit non-finite arithmetic for an extreme scenario range', () => {
    const data = scenario()
    data.assumptions.gpu_rental_price_usd_per_hour.high = Number.MAX_VALUE
    expect(data.calculate).toThrow(RangeError)
  })
})
