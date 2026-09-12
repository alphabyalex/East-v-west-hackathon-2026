import type { MockResponse, ScenarioInputs, SourcedValue } from './types'

/** These are deliberately illustrative assumptions, never measurements or clauses. */
export const mockAssumption = <T>(value: T, ref: string): SourcedValue<T> => ({
  value,
  source_type: 'assumption',
  ref: `mock://illustrative/${ref}`,
})

// Fixed fan-chart fixture: [year, p10, p50, p90, p99], in hours/year.
// These tuples are authored demo values. No simulation or random sampling runs here.
const baseline = [
  [1, 102, 200, 336, 510],
  [2, 104, 203, 344, 525],
  [3, 108, 211, 352, 540],
  [4, 105, 208, 358, 558],
  [5, 112, 219, 372, 575],
  [6, 114, 223, 381, 591],
  [7, 117, 226, 393, 612],
  [8, 116, 224, 401, 630],
  [9, 121, 237, 415, 655],
  [10, 125, 244, 428, 680],
  [11, 124, 241, 440, 705],
  [12, 129, 253, 452, 730],
  [13, 130, 255, 466, 758],
  [14, 132, 260, 480, 786],
  [15, 131, 257, 491, 812],
  [16, 135, 267, 505, 842],
  [17, 138, 272, 519, 872],
  [18, 139, 275, 531, 902],
  [19, 141, 281, 546, 934],
  [20, 144, 286, 560, 965],
] as const

// Labels identify SPP-region examples, not validated pricing nodes or interconnections.
const locations = [
  { id: 'spp-wichita-demo', label: 'Wichita, KS · illustrative', scale: 1 },
  { id: 'spp-oklahoma-city-demo', label: 'Oklahoma City, OK · illustrative', scale: 1.12 },
  { id: 'spp-lincoln-demo', label: 'Lincoln, NE · illustrative', scale: 0.9 },
] as const

export const defaultInputs: ScenarioInputs = {
  location_id: 'spp-wichita-demo',
  load_mw: 100,
  contract_years: 10,
  flexibility_percent: 60,
  site_exposure: 0.4,
  // Round UI-development placeholders, not sourced economic estimates.
  // Replace from the team's docs/ASSUMPTIONS.md once it is available on main.
  firm_wait_years: 3,
  gpu_per_mw: 1_000,
  gpu_hour_value_usd: 2,
  early_margin_usd_per_mw_year: 500_000,
}

export const mockResponse: MockResponse = {
  schema_version: '1.0.0',
  grid_operator: 'SPP',
  mode: 'illustrative',
  locations: locations.map(({ id, label, scale }) => ({
    id,
    label,
    annual_series: baseline.map(([year, p10, p50, p90, p99]) => ({
      year: mockAssumption(year, `locations/${id}/annual_series/${year}/year`),
      p10: mockAssumption(p10 * scale, `locations/${id}/annual_series/${year}/p10-hours-per-year`),
      p50: mockAssumption(p50 * scale, `locations/${id}/annual_series/${year}/p50-hours-per-year`),
      p90: mockAssumption(p90 * scale, `locations/${id}/annual_series/${year}/p90-hours-per-year`),
      p99: mockAssumption(p99 * scale, `locations/${id}/annual_series/${year}/p99-hours-per-year`),
    })),
  })),
  defaults: {
    location_id: mockAssumption(defaultInputs.location_id, 'inputs/location_id'),
    load_mw: mockAssumption(defaultInputs.load_mw, 'inputs/load_mw'),
    contract_years: mockAssumption(defaultInputs.contract_years, 'inputs/contract_years'),
    flexibility_percent: mockAssumption(defaultInputs.flexibility_percent, 'inputs/flexibility_percent'),
    site_exposure: mockAssumption(defaultInputs.site_exposure, 'inputs/site_exposure'),
    firm_wait_years: mockAssumption(defaultInputs.firm_wait_years, 'economics-placeholder/inputs/firm_wait_years?pending=docs/ASSUMPTIONS.md'),
    gpu_per_mw: mockAssumption(defaultInputs.gpu_per_mw, 'economics-placeholder/inputs/gpu_per_mw?pending=docs/ASSUMPTIONS.md'),
    gpu_hour_value_usd: mockAssumption(defaultInputs.gpu_hour_value_usd, 'economics-placeholder/inputs/gpu_hour_value_usd?pending=docs/ASSUMPTIONS.md'),
    early_margin_usd_per_mw_year: mockAssumption(defaultInputs.early_margin_usd_per_mw_year, 'economics-placeholder/inputs/early_margin_usd_per_mw_year?pending=docs/ASSUMPTIONS.md'),
  },
  decision_policy: {
    close_call_fraction: mockAssumption(0.05, 'decision_policy/close_call_fraction-of-early-access-value'),
  },
}
