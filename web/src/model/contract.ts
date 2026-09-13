import type { ScenarioInputs, Source } from './types'

/** Canonical POST /api/estimate body; docs/BUILD_PLAN.md section 2. */
export interface EstimateRequest {
  location_id: string
  load_mw: number
  term_years: number
  flexibility_split: number
  site_exposure: number
  vpp_solar_homes?: number
}

export interface Quantiles<T = number> {
  p50: T
  p90: T
  p99: T
}

/** Block provenance also applies to each numeric descendant of that block. */
export interface EstimateResponse {
  inputs_echo: EstimateRequest
  modeled_exposure: Quantiles & {
    unit: 'hours/year'
    worst_contiguous_outage_hours: number
    by_year: Array<Quantiles & { year: number }>
    source: Source
  }
  confidence: {
    level: 'High' | 'Medium' | 'Low'
    score: number
    basis: string
    source: Source
  }
  economics: {
    gpus_per_mw: number
    interruptible_mw: number
    vpp_offset_mw: number
    net_interruptible_mw: number
    lost_gpu_hours_per_year: Quantiles
    annual_cost_usd: Quantiles
    vpp_arbitrage_revenue_usd_per_year: Quantiles
    value_of_early_connection_usd: number
    /** No finite threshold exists when cost per exposure hour is zero. */
    breakeven_exposure_hours_per_year: number | null
    decision: 'worth_it' | 'not_worth_it' | 'close_call'
    source: Source
  }
  tariff: {
    operator: 'SPP'
    service: string
    curtailment_triggers: Array<{ text: string; observable: boolean; source: Source }>
  }
}

/** Local mock controls are not extra fields on the canonical API request. */
export type MockEconomicInputs = Pick<ScenarioInputs,
  'firm_wait_years' | 'gpu_per_mw' | 'gpu_hour_value_usd' | 'early_margin_usd_per_mw_year'> & {
  vpp_battery_discharge_mw_per_home: number
  vpp_arbitrage_revenue_usd_per_mwh: number
}
