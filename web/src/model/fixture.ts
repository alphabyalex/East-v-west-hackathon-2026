import type { MockResponse, ScenarioInputs, SourcedValue } from './types'
import economicSnapshot from './economics-assumptions.json'

/** These are deliberately illustrative assumptions, never measurements or clauses. */
export const mockAssumption = <T>(value: T, ref: string): SourcedValue<T> => ({
  value,
  source_type: 'assumption',
  ref: `mock://illustrative/${ref}`,
})

// Bundled snapshot of the marked docs/ASSUMPTIONS.md JSON for offline fallback.
// API mode replaces this snapshot with validated metadata from the same server file.
const economicDefault = (key: Exclude<keyof typeof economicSnapshot, 'schema_version' | 'status'>): SourcedValue => ({
  value: economicSnapshot[key].value,
  source_type: 'assumption',
  ref: economicSnapshot[key].ref,
})

// Fixed quantile fixture: [year, p50, p90, p99], in hours/year.
// These tuples are authored demo values. No simulation or random sampling runs here.
const baseline = [
  [1, 200, 336, 510],
  [2, 203, 344, 525],
  [3, 211, 352, 540],
  [4, 208, 358, 558],
  [5, 219, 372, 575],
  [6, 223, 381, 591],
  [7, 226, 393, 612],
] as const

// Labels identify SPP-region examples, not validated pricing nodes or interconnections.
const locations = [
  { id: 'SPP_SYSTEM', label: 'SPP system aggregate', scale: 1 },
  { id: 'spp-wichita-demo', label: 'Wichita, KS · illustrative', scale: 1 },
  { id: 'spp-oklahoma-city-demo', label: 'Oklahoma City, OK · illustrative', scale: 1.12 },
  { id: 'spp-lincoln-demo', label: 'Lincoln, NE · illustrative', scale: 0.9 },
] as const

export const defaultInputs: ScenarioInputs = {
  location_id: 'SPP_SYSTEM',
  load_mw: 100,
  contract_years: 7,
  flexibility_percent: 60,
  site_exposure: 0.4,
  firm_wait_years: economicSnapshot.early_connection_years.value,
  gpu_per_mw: economicSnapshot.gpus_per_mw.value,
  gpu_hour_value_usd: economicSnapshot.gpu_rental_price_usd_per_hour.value,
  early_margin_usd_per_mw_year: economicSnapshot.early_margin_usd_per_mw_year.value,
  vpp_solar_homes: 0,
}

export const mockResponse: MockResponse = {
  schema_version: '1.0.0',
  grid_operator: 'SPP',
  mode: 'illustrative',
  locations: locations.map(({ id, label, scale }) => ({
    id,
    label,
    annual_series: baseline.map(([year, p50, p90, p99]) => ({
      year: mockAssumption(year, `locations/${id}/annual_series/${year}/year`),
      p50: mockAssumption(p50 * scale, `locations/${id}/annual_series/${year}/p50-hours-per-year`),
      p90: mockAssumption(p90 * scale, `locations/${id}/annual_series/${year}/p90-hours-per-year`),
      p99: mockAssumption(p99 * scale, `locations/${id}/annual_series/${year}/p99-hours-per-year`),
    })),
  })),
  defaults: {
    location_id: { value: defaultInputs.location_id, source_type: 'assumption', ref: 'user://scenario/location_id; SPP_SYSTEM selects the system aggregate, not a node or site estimate' },
    load_mw: mockAssumption(defaultInputs.load_mw, 'inputs/load_mw'),
    contract_years: mockAssumption(defaultInputs.contract_years, 'inputs/contract_years'),
    flexibility_percent: mockAssumption(defaultInputs.flexibility_percent, 'inputs/flexibility_percent'),
    site_exposure: mockAssumption(defaultInputs.site_exposure, 'inputs/site_exposure'),
    firm_wait_years: economicDefault('early_connection_years'),
    gpu_per_mw: economicDefault('gpus_per_mw'),
    gpu_hour_value_usd: economicDefault('gpu_rental_price_usd_per_hour'),
    early_margin_usd_per_mw_year: economicDefault('early_margin_usd_per_mw_year'),
    vpp_solar_homes: mockAssumption(defaultInputs.vpp_solar_homes, 'inputs/vpp_solar_homes'),
  },
  decision_policy: {
    close_call_fraction: economicDefault('close_call_fraction'),
  },
}
