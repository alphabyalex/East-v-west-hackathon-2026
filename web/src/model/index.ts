import { defaultInputs, mockAssumption, mockResponse } from './fixture'
import type { ScenarioInputs, ScenarioResult, SourcedInputs } from './types'

export { defaultInputs, mockAssumption, mockResponse }
export type * from './types'

function validateInputs(inputs: ScenarioInputs) {
  for (const [key, value] of Object.entries(inputs)) {
    if (key === 'location_id') continue
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      throw new RangeError(`${key} must be a finite non-negative number.`)
    }
  }
  if (inputs.site_exposure > 1) throw new RangeError('site_exposure must be between 0 and 1.')
  if (inputs.flexibility_percent > 100) throw new RangeError('flexibility_percent must be between 0 and 100.')
  if (!Number.isInteger(inputs.contract_years) || inputs.contract_years < 1 || inputs.contract_years > 20) {
    throw new RangeError('contract_years must be an integer between 1 and 20.')
  }
}

/**
 * Lightweight, deterministic arithmetic over a fixed illustrative fixture.
 * No training, sampling, API requests, or site-specific inference happens here.
 */
export function deriveScenario(inputs: ScenarioInputs): ScenarioResult {
  validateInputs(inputs)
  const location = mockResponse.locations.find((entry) => entry.id === inputs.location_id)
  if (!location) throw new RangeError('location_id must identify a supplied SPP example.')

  const sourceInputs = Object.fromEntries(
    Object.entries(inputs).map(([key, value]) => [key, mockAssumption(value, `inputs/${key}`)]),
  ) as SourcedInputs
  const scenarioRef = new URLSearchParams(
    Object.entries(inputs).map(([key, value]) => [key, String(value)]),
  ).toString()
  const derived = <T>(value: T, field: string) =>
    mockAssumption(value, `derived/${field}?${scenarioRef}`)

  const selectedYears = location.annual_series.slice(0, inputs.contract_years)
  const averageBaseline = (quantile: 'p50' | 'p90' | 'p99') =>
    selectedYears.reduce((sum, row) => sum + row[quantile].value, 0) / selectedYears.length

  const annualExposure = {
    p50: derived(averageBaseline('p50') * inputs.site_exposure, 'annual_exposure/p50'),
    p90: derived(averageBaseline('p90') * inputs.site_exposure, 'annual_exposure/p90'),
    p99: derived(averageBaseline('p99') * inputs.site_exposure, 'annual_exposure/p99'),
  }
  const interruptibleMw = inputs.load_mw * inputs.flexibility_percent / 100
  const annualLostGpuHours = annualExposure.p50.value * interruptibleMw * inputs.gpu_per_mw
  const annualLoss = annualLostGpuHours * inputs.gpu_hour_value_usd
  const termLoss = annualLoss * inputs.contract_years
  const earlyAccess = Math.min(inputs.firm_wait_years, inputs.contract_years)
    * inputs.load_mw * inputs.early_margin_usd_per_mw_year
  const netValue = earlyAccess - termLoss
  const costPerExposureHour = interruptibleMw * inputs.gpu_per_mw * inputs.gpu_hour_value_usd
  const breakEvenHours = costPerExposureHour === 0
    ? null
    : earlyAccess / (inputs.contract_years * costPerExposureHour)
  const breakEvenFactor = breakEvenHours === null ? null : breakEvenHours / averageBaseline('p50')
  const tolerance = earlyAccess * mockResponse.decision_policy.close_call_fraction.value
  const decision = Math.abs(netValue) <= tolerance
    ? 'close call'
    : netValue > 0 ? 'worth it' : 'not worth it'

  return {
    inputs: sourceInputs,
    annual_exposure: annualExposure,
    annual_series: selectedYears.map((row) => ({
      year: row.year,
      p10: derived(row.p10.value * inputs.site_exposure, `annual_series/${row.year.value}/p10`),
      p50: derived(row.p50.value * inputs.site_exposure, `annual_series/${row.year.value}/p50`),
      p90: derived(row.p90.value * inputs.site_exposure, `annual_series/${row.year.value}/p90`),
      p99: derived(row.p99.value * inputs.site_exposure, `annual_series/${row.year.value}/p99`),
    })),
    economics: {
      interruptible_mw: derived(interruptibleMw, 'economics/interruptible_mw'),
      annual_lost_gpu_hours: derived(annualLostGpuHours, 'economics/annual_lost_gpu_hours'),
      annual_loss_usd: derived(annualLoss, 'economics/annual_loss_usd'),
      term_loss_usd: derived(termLoss, 'economics/term_loss_usd'),
      early_access_value_usd: derived(earlyAccess, 'economics/early_access_value_usd'),
      net_value_usd: derived(netValue, 'economics/net_value_usd'),
      break_even_exposure_hours: derived(breakEvenHours, 'economics/break_even_exposure_hours'),
      break_even_site_exposure: derived(breakEvenFactor, 'economics/break_even_site_exposure'),
    },
    decision,
    decision_policy: mockResponse.decision_policy,
  }
}
