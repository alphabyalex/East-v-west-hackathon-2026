import { defaultInputs, mockResponse } from './fixture'
import economicSnapshot from './economics-assumptions.json'
import type { EstimateRequest, EstimateResponse, MockEconomicInputs, Quantiles } from './contract'
import type { ScenarioInputs, ScenarioResult, Source, SourcedInputs, SourcedValue } from './types'

function assertFiniteNonNegative(value: number, field: string) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw new RangeError(`${field} must be a finite non-negative number.`)
  }
}

function validateRequest(request: EstimateRequest) {
  assertFiniteNonNegative(request.load_mw, 'load_mw')
  assertFiniteNonNegative(request.site_exposure, 'site_exposure')
  assertFiniteNonNegative(request.flexibility_split, 'flexibility_split')
  if (request.site_exposure > 1) throw new RangeError('site_exposure must be between 0 and 1.')
  if (request.flexibility_split > 1) throw new RangeError('flexibility_split must be between 0 and 1.')
  if (!Number.isInteger(request.term_years) || request.term_years < 1 || request.term_years > 7) {
    throw new RangeError('term_years must be an integer between 1 and 7.')
  }
}

/** Maps UI names/percentages without leaking local mock economics into the API body. */
export function toEstimateRequest(inputs: ScenarioInputs): EstimateRequest {
  // Explicitly include all six canonical fields.
  const request: EstimateRequest = {
    location_id: inputs.location_id,
    load_mw: inputs.load_mw,
    term_years: inputs.contract_years,
    flexibility_split: inputs.flexibility_percent / 100,
    site_exposure: inputs.site_exposure,
    vpp_solar_homes: inputs.vpp_solar_homes,
  }
  validateRequest(request)
  return request
}

function economicInputs(overrides: Partial<MockEconomicInputs>): MockEconomicInputs {
  const values = {
    firm_wait_years: overrides.firm_wait_years ?? defaultInputs.firm_wait_years,
    gpu_per_mw: overrides.gpu_per_mw ?? defaultInputs.gpu_per_mw,
    gpu_hour_value_usd: overrides.gpu_hour_value_usd ?? defaultInputs.gpu_hour_value_usd,
    early_margin_usd_per_mw_year: overrides.early_margin_usd_per_mw_year ?? defaultInputs.early_margin_usd_per_mw_year,
    vpp_battery_discharge_mw_per_home: overrides.vpp_battery_discharge_mw_per_home ?? economicSnapshot.vpp_battery_discharge_mw_per_home.value,
    vpp_arbitrage_revenue_usd_per_mwh: overrides.vpp_arbitrage_revenue_usd_per_mwh ?? economicSnapshot.vpp_arbitrage_revenue_usd_per_mwh.value,
  }
  Object.entries(values).forEach(([field, value]) => assertFiniteNonNegative(value, field))
  return values
}

function mapQuantiles<T>(map: (quantile: keyof Quantiles) => T): Quantiles<T> {
  return { p50: map('p50'), p90: map('p90'), p99: map('p99') }
}

/**
 * Deterministic stand-in for POST /api/estimate. It performs cheap fixture arithmetic
 * only: no network, model training, inference, or Monte Carlo execution.
 * Summary quantiles are averages of supplied marginal annual quantiles. Summing a
 * marginal quantile path is a comparison proxy, not a quantile of total term loss.
 */
export function createMockEstimate(
  request: EstimateRequest,
  localEconomics: Partial<MockEconomicInputs> = {},
  decisionPolicy: SourcedValue = mockResponse.decision_policy.close_call_fraction,
): EstimateResponse {
  validateRequest(request)
  const location = mockResponse.locations.find((entry) => entry.id === request.location_id)
  if (!location) throw new RangeError('location_id must identify a supplied SPP example.')
  const local = economicInputs(localEconomics)
  const scenarioRef = new URLSearchParams(
    Object.entries(request).map(([key, value]) => [key, String(value)]),
  ).toString()
  const source: Source = {
    source_type: 'assumption',
    ref: `mock://illustrative/api/estimate/modeled_exposure?${scenarioRef}`,
  }
  const byYear = location.annual_series.slice(0, request.term_years).map((row) => ({
    year: row.year.value,
    ...mapQuantiles((quantile) => row[quantile].value * request.site_exposure),
  }))
  const summary = mapQuantiles((quantile) =>
    byYear.reduce((sum, row) => sum + row[quantile], 0) / byYear.length)
  const interruptibleMw = request.load_mw * request.flexibility_split

  // Sustainability VPP Logic
  const vppOffsetMw = (request.vpp_solar_homes ?? 0) * local.vpp_battery_discharge_mw_per_home
  const netInterruptibleMw = Math.max(0, interruptibleMw - vppOffsetMw)

  const lostGpuHours = mapQuantiles((quantile) => summary[quantile] * netInterruptibleMw * local.gpu_per_mw)
  const annualCost = mapQuantiles((quantile) => lostGpuHours[quantile] * local.gpu_hour_value_usd)
  const vppArbitrageRevenue = mapQuantiles((quantile) => summary[quantile] * vppOffsetMw * local.vpp_arbitrage_revenue_usd_per_mwh)

  // Net annual cost subtracts VPP arbitrage revenue
  const annualNetCost = mapQuantiles((quantile) => annualCost[quantile] - vppArbitrageRevenue[quantile])

  const benefit = Math.min(local.firm_wait_years, request.term_years)
    * request.load_mw * local.early_margin_usd_per_mw_year
  const costPerExposureHour = netInterruptibleMw * local.gpu_per_mw * local.gpu_hour_value_usd
  const revenuePerExposureHour = vppOffsetMw * local.vpp_arbitrage_revenue_usd_per_mwh
  const netCostPerExposureHour = costPerExposureHour - revenuePerExposureHour

  const tolerance = decisionPolicy.value
  assertFiniteNonNegative(tolerance, 'close_call_fraction')
  if (tolerance >= 1) throw new RangeError('close_call_fraction must be less than 1.')
  const decision: EstimateResponse['economics']['decision'] = annualNetCost.p50 * request.term_years > benefit * (1 + tolerance)
    ? 'not_worth_it'
    : annualNetCost.p90 * request.term_years < benefit * (1 - tolerance) ? 'worth_it' : 'close_call'
  const economicsRef = new URLSearchParams({
    ...Object.fromEntries(Object.entries(request).map(([key, value]) => [key, String(value)])),
    ...Object.fromEntries(Object.entries(local).map(([key, value]) => [key, String(value)])),
  }).toString()

  return {
    inputs_echo: { ...request },
    modeled_exposure: {
      unit: 'hours/year',
      ...summary,
      // Authored round placeholder; not an extracted or simulated outage duration.
      worst_contiguous_outage_hours: 40 * request.site_exposure,
      by_year: byYear,
      source,
    },
    confidence: {
      level: 'Medium',
      score: 0.5,
      basis: 'illustrative_fixture_not_ensemble_inference',
      source: { source_type: 'assumption', ref: 'mock://illustrative/confidence/fixed-placeholder-not-model-confidence' },
    },
    economics: {
      gpus_per_mw: local.gpu_per_mw,
      interruptible_mw: interruptibleMw,
      vpp_offset_mw: vppOffsetMw,
      net_interruptible_mw: netInterruptibleMw,
      lost_gpu_hours_per_year: lostGpuHours,
      annual_cost_usd: annualNetCost,
      vpp_arbitrage_revenue_usd_per_year: vppArbitrageRevenue,
      value_of_early_connection_usd: benefit,
      breakeven_exposure_hours_per_year: netCostPerExposureHour <= 0
        ? null : benefit / (request.term_years * netCostPerExposureHour),
      decision,
      source: {
        source_type: 'assumption',
        ref: `mock://illustrative/api/estimate/economics?${economicsRef}; exposure_source=${source.ref}; economic_inputs=local assumptions, defaults documented in docs/ASSUMPTIONS.md; close_call_fraction=${tolerance}; policy_source=${decisionPolicy.ref}`,
      },
    },
    tariff: {
      operator: 'SPP',
      service: 'CHILLS (mock; not extracted)',
      curtailment_triggers: [{
        text: 'Mock trigger placeholder; no tariff clause has been extracted or verified.',
        observable: false,
        source: { source_type: 'assumption', ref: 'mock://illustrative/tariff/placeholder-not-extracted' },
      }],
    },
  }
}

function sourced<T>(value: T, source: Source, path: string): SourcedValue<T> {
  return { value, source_type: source.source_type, ref: `${source.ref}#${path}` }
}

/**
 * Wraps canonical block provenance around individual UI values. API exposure values
 * are already site-scaled: this adapter must never multiply them by site_exposure.
 */
export function adaptEstimateResponse(
  response: EstimateResponse,
  localEconomics: Partial<MockEconomicInputs> = {},
  decisionPolicy: SourcedValue = mockResponse.decision_policy.close_call_fraction,
): ScenarioResult {
  const request = response.inputs_echo
  validateRequest(request)
  const local = economicInputs(localEconomics)
  const exposure = response.modeled_exposure
  const economics = response.economics
  const uiInputs: ScenarioInputs = {
    location_id: request.location_id,
    load_mw: request.load_mw,
    contract_years: request.term_years,
    flexibility_percent: request.flexibility_split * 100,
    site_exposure: request.site_exposure,
    vpp_solar_homes: request.vpp_solar_homes ?? 0,
    ...local,
    gpu_per_mw: economics.gpus_per_mw,
  }
  const inputs = Object.fromEntries(Object.entries(uiInputs).map(([field, value]) => [field, {
    value,
    source_type: 'assumption',
    ref: `user://estimate/inputs/${field}`,
  }])) as SourcedInputs
  inputs.gpu_per_mw = sourced(economics.gpus_per_mw, economics.source, 'economics/gpus_per_mw')
  const annualExposure = mapQuantiles((quantile) => sourced(exposure[quantile], exposure.source, `modeled_exposure/${quantile}`))
  const annualLoss = mapQuantiles((quantile) => sourced(economics.annual_cost_usd[quantile], economics.source, `economics/annual_cost_usd/${quantile}`))
  const annualGpuHours = mapQuantiles((quantile) => sourced(economics.lost_gpu_hours_per_year[quantile], economics.source, `economics/lost_gpu_hours_per_year/${quantile}`))
  const termLoss = economics.annual_cost_usd.p50 * request.term_years
  const breakEvenHours = economics.breakeven_exposure_hours_per_year
  // A zero-exposure response contains no recoverable baseline: leave its factor unset.
  const breakEvenFactor = breakEvenHours === null || request.site_exposure === 0 || exposure.p50 === 0
    ? null : breakEvenHours / (exposure.p50 / request.site_exposure)

  return {
    inputs,
    annual_exposure: annualExposure,
    annual_series: exposure.by_year.map((row, index) => ({
      year: sourced(row.year, exposure.source, `modeled_exposure/by_year/${index}/year`),
      ...mapQuantiles((quantile) => sourced(row[quantile], exposure.source, `modeled_exposure/by_year/${index}/${quantile}`)),
    })),
    confidence: {
      level: response.confidence.level,
      score: sourced(response.confidence.score, response.confidence.source, 'confidence/score'),
      basis: response.confidence.basis,
      source: response.confidence.source,
    },
    worst_contiguous_exposure: sourced(exposure.worst_contiguous_outage_hours, exposure.source, 'modeled_exposure/worst_contiguous_outage_hours'),
    tariff: response.tariff,
    canonical_response: response,
    economics: {
      interruptible_mw: sourced(economics.interruptible_mw, economics.source, 'economics/interruptible_mw'),
      vpp_offset_mw: sourced(economics.vpp_offset_mw, economics.source, 'economics/vpp_offset_mw'),
      net_interruptible_mw: sourced(economics.net_interruptible_mw, economics.source, 'economics/net_interruptible_mw'),
      vpp_arbitrage_revenue_usd: sourced(economics.vpp_arbitrage_revenue_usd_per_year.p50, economics.source, 'economics/vpp_arbitrage_revenue_usd_per_year/p50'),
      annual_lost_gpu_hours: annualGpuHours.p50,
      annual_loss_usd: annualLoss.p50,
      annual_lost_gpu_hours_by_quantile: annualGpuHours,
      annual_loss_by_quantile: annualLoss,
      term_loss_usd: sourced(termLoss, economics.source, 'derived/annual_cost_usd/p50-times-term_years'),
      early_access_value_usd: sourced(economics.value_of_early_connection_usd, economics.source, 'economics/value_of_early_connection_usd'),
      net_value_usd: sourced(economics.value_of_early_connection_usd - termLoss, economics.source, 'derived/net_term_value'),
      break_even_exposure_hours: sourced(breakEvenHours, economics.source, 'economics/breakeven_exposure_hours_per_year'),
      break_even_site_exposure: sourced(breakEvenFactor, economics.source, 'derived/breakeven_site_exposure'),
    },
    decision: { worth_it: 'worth it', not_worth_it: 'not worth it', close_call: 'close call' }[economics.decision] as ScenarioResult['decision'],
    decision_policy: { close_call_fraction: decisionPolicy },
  }
}
