/** Authored contract-test fixture, never imported by the application. */
import type { ScenarioInputs } from '../model';
import type { LocationResult } from './locationEstimator';

export function locationFixture(inputs: ScenarioInputs): LocationResult {
  const source = { source_type: 'model' as const, ref: 'test://authored-location-report' };
  return {
    inputs_echo: { ...inputs }, report_id: 'test-report', location: { name: 'Test City, Kansas', latitude: 38, longitude: -98 },
    model_version: 'test-model', created_utc: '2026-09-13T00:00:00Z', confidence: { level: 'Low' },
    location_data_note: 'Authored test location.', limitations: ['Not measured data.'], source,
    assumption_source: { ...source, source_type: 'assumption' }, economic_source: { ...source, source_type: 'assumption' },
    exposure: { annual_expected_hours: 400 * inputs.site_exposure, regional_expected_hours: 400,
      term_expected_hours: 400 * inputs.site_exposure * inputs.contract_years,
      annual_energy_mwh: 400 * inputs.site_exposure * inputs.load_mw * inputs.flexibility_percent / 100 },
    economics: { interruptible_mw: 60, vpp_offset_mw: 0, net_interruptible_mw: 60, annual_gpu_hours: 100,
      vpp_annual_revenue_usd: 0, annual_cost_usd: 200, term_cost_usd: 1400, early_access_value_usd: 2000,
      net_value_usd: 600, break_even_hours: 200 }, calculation_inputs: {}, provenance: {},
  };
}
