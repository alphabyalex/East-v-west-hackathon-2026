import type { EstimateResponse } from './contract'

export type SourceType = 'data' | 'clause' | 'assumption' | 'model'

export interface Source {
  source_type: SourceType
  ref: string
}

export interface SourcedValue<T = number> extends Source {
  value: T
}

export interface ScenarioInputs {
  location_id: string
  load_mw: number
  contract_years: number
  flexibility_percent: number
  site_exposure: number
  firm_wait_years: number
  gpu_per_mw: number
  gpu_hour_value_usd: number
  early_margin_usd_per_mw_year: number
}

export type SourcedInputs = {
  [K in keyof ScenarioInputs]: SourcedValue<ScenarioInputs[K]>
}

export interface BaselineYear {
  year: SourcedValue
  p50: SourcedValue
  p90: SourcedValue
  p99: SourcedValue
}

export interface MockResponse {
  schema_version: '1.0.0'
  grid_operator: 'SPP'
  mode: 'illustrative'
  locations: Array<{
    id: string
    label: string
    annual_series: BaselineYear[]
  }>
  defaults: SourcedInputs
  decision_policy: { close_call_fraction: SourcedValue }
}

export interface ScenarioResult {
  inputs: SourcedInputs
  annual_exposure: {
    p50: SourcedValue
    p90: SourcedValue
    p99: SourcedValue
  }
  annual_series: BaselineYear[]
  confidence: {
    level: EstimateResponse['confidence']['level']
    score: SourcedValue
    basis: string
    source: Source
  }
  worst_contiguous_exposure: SourcedValue
  tariff: EstimateResponse['tariff']
  canonical_response: EstimateResponse
  economics: {
    interruptible_mw: SourcedValue
    annual_lost_gpu_hours: SourcedValue
    annual_loss_usd: SourcedValue
    annual_lost_gpu_hours_by_quantile: { p50: SourcedValue; p90: SourcedValue; p99: SourcedValue }
    annual_loss_by_quantile: { p50: SourcedValue; p90: SourcedValue; p99: SourcedValue }
    term_loss_usd: SourcedValue
    early_access_value_usd: SourcedValue
    net_value_usd: SourcedValue
    break_even_exposure_hours: SourcedValue<number | null>
    break_even_site_exposure: SourcedValue<number | null>
  }
  decision: 'worth it' | 'not worth it' | 'close call'
  decision_policy: { close_call_fraction: SourcedValue }
}
