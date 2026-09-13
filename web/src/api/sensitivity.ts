import { postEstimate, type EstimateClientOptions } from './client'
import { validateEconomicConsistency, type EconomicsAssumptions } from './assumptions'
import type { EstimateResponse, Quantiles, ScenarioInputs } from '../model'
import { buildSensitivity } from '../model/sensitivity'

/** Undo only the canonical site's linear multiplier; never infer from dollar loss. */
function restoreFullExposure(response: EstimateResponse, assumptions: EconomicsAssumptions): EstimateResponse {
  const factor = response.inputs_echo.site_exposure
  if (factor <= 0) throw new RangeError('Zero exposure requires a separate full-exposure response.')
  if (factor === 1) return response
  const full = structuredClone(response)
  full.inputs_echo.site_exposure = 1
  const unscale = (values: Quantiles): Quantiles => ({
    p50: values.p50 / factor, p90: values.p90 / factor, p99: values.p99 / factor,
  })
  Object.assign(full.modeled_exposure, unscale(response.modeled_exposure))
  full.modeled_exposure.worst_contiguous_outage_hours /= factor
  full.modeled_exposure.by_year = response.modeled_exposure.by_year.map(row => ({ year: row.year, ...unscale(row) }))
  
  const interruptibleMw = full.inputs_echo.load_mw * full.inputs_echo.flexibility_split
  const vppOffsetMw = (full.inputs_echo.vpp_solar_homes ?? 0) * assumptions.vpp_battery_discharge_mw_per_home.value
  const netInterruptibleMw = Math.max(0, interruptibleMw - vppOffsetMw)

  for (const quantile of ['p50', 'p90', 'p99'] as const) {
    const gpuHours = full.modeled_exposure[quantile] * netInterruptibleMw * full.economics.gpus_per_mw
    full.economics.lost_gpu_hours_per_year[quantile] = gpuHours
    
    const annualLoss = gpuHours * assumptions.gpu_rental_price_usd_per_hour.value
    const vppRevenue = full.modeled_exposure[quantile] * vppOffsetMw * assumptions.vpp_arbitrage_revenue_usd_per_mwh.value
    full.economics.vpp_arbitrage_revenue_usd_per_year[quantile] = vppRevenue
    full.economics.annual_cost_usd[quantile] = annualLoss - vppRevenue
  }
  const benefit = full.economics.value_of_early_connection_usd
  const tolerance = assumptions.close_call_fraction.value
  const term = full.inputs_echo.term_years
  full.economics.decision = full.economics.annual_cost_usd.p50 * term > benefit * (1 + tolerance)
    ? 'not_worth_it'
    : full.economics.annual_cost_usd.p90 * term < benefit * (1 - tolerance) ? 'worth_it' : 'close_call'
  // Sources continue to cite the response actually received. This internal object
  // is an arithmetic normalization, not a second model run or an HTTP response.
  return full
}

/** One extra cheap API read is necessary only at the irrecoverable zero endpoint. */
export async function buildEstimateSensitivity(
  response: EstimateResponse, assumptions: EconomicsAssumptions, options: EstimateClientOptions = {},
) {
  validateEconomicConsistency(response, assumptions)
  const full = response.inputs_echo.site_exposure === 0
    ? await postEstimate({ ...response.inputs_echo, site_exposure: 1 }, options)
    : restoreFullExposure(response, assumptions)
  validateEconomicConsistency(full, assumptions)
  const request = response.inputs_echo
  const inputs: ScenarioInputs = {
    location_id: request.location_id, load_mw: request.load_mw,
    contract_years: request.term_years, flexibility_percent: request.flexibility_split * 100,
    site_exposure: request.site_exposure,
    vpp_solar_homes: request.vpp_solar_homes ?? 0,
    firm_wait_years: assumptions.early_connection_years.value,
    gpu_per_mw: assumptions.gpus_per_mw.value,
    gpu_hour_value_usd: assumptions.gpu_rental_price_usd_per_hour.value,
    early_margin_usd_per_mw_year: assumptions.early_margin_usd_per_mw_year.value,
  }
  return buildSensitivity(response, full, inputs, assumptions)
}
