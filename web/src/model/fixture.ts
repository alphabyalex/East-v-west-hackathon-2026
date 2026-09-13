import type { MockResponse, ScenarioInputs, SourcedValue } from './types'

/** These are deliberately illustrative assumptions, never measurements or clauses. */
export const mockAssumption = <T>(value: T, ref: string): SourcedValue<T> => ({
  value,
  source_type: 'assumption',
  ref: `mock://illustrative/${ref}`,
})

/** A real, cited team-set assumption - still an assumption, but sourced, not an invented placeholder. */
export const citedAssumption = <T>(value: T, ref: string): SourcedValue<T> => ({
  value,
  source_type: 'assumption',
  ref: `docs/ASSUMPTIONS.md${ref}`,
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
  { id: 'spp-wichita-demo', label: 'Wichita, KS · illustrative', scale: 1 },
  { id: 'spp-oklahoma-city-demo', label: 'Oklahoma City, OK · illustrative', scale: 1.12 },
  { id: 'spp-lincoln-demo', label: 'Lincoln, NE · illustrative', scale: 0.9 },
] as const

export const defaultInputs: ScenarioInputs = {
  location_id: 'spp-wichita-demo',
  load_mw: 100,
  contract_years: 7,
  flexibility_percent: 60,
  site_exposure: 0.4,
  // Sourced from docs/ASSUMPTIONS.md: gpu_per_mw (section 1, grid-interconnection
  // basis), gpu_hour_value_usd (section 2, H100 cross-provider composite),
  // firm_wait_years (section 3, derived firm-vs-flexible gap). early_margin_usd_per_mw_year
  // is a 3% assumed operating margin on the doc's sourced gross-revenue derivation -
  // no public margin figure exists for this business model, so it stays a labeled
  // ASSUMPTION layered on top of a sourced revenue number (see section 4).
  firm_wait_years: 4,
  gpu_per_mw: 575,
  gpu_hour_value_usd: 3,
  early_margin_usd_per_mw_year: 317_000,
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
    location_id: mockAssumption(defaultInputs.location_id, 'inputs/location_id'),
    load_mw: mockAssumption(defaultInputs.load_mw, 'inputs/load_mw'),
    contract_years: mockAssumption(defaultInputs.contract_years, 'inputs/contract_years'),
    flexibility_percent: mockAssumption(defaultInputs.flexibility_percent, 'inputs/flexibility_percent'),
    site_exposure: mockAssumption(defaultInputs.site_exposure, 'inputs/site_exposure'),
    firm_wait_years: citedAssumption(defaultInputs.firm_wait_years, '#early-connection-years'),
    gpu_per_mw: citedAssumption(defaultInputs.gpu_per_mw, '#gpus-per-mw'),
    gpu_hour_value_usd: citedAssumption(defaultInputs.gpu_hour_value_usd, '#gpu-rental-price'),
    // Still placeholder-category: a 3% assumed margin on sourced revenue, not itself
    // a direct citation - matches docs/ASSUMPTIONS.md's early_margin_usd_per_mw_year entry.
    early_margin_usd_per_mw_year: mockAssumption(defaultInputs.early_margin_usd_per_mw_year, 'economics-placeholder/docs/ASSUMPTIONS.md#early-margin'),
  },
  decision_policy: {
    close_call_fraction: mockAssumption(0.05, 'decision_policy/close_call_fraction-of-early-access-value'),
  },
}
