import { describe, expect, it } from 'vitest';
import { postEstimate } from './client';
import { getLocations } from './locations';
import { getEconomicsAssumptions, validateEconomicConsistency } from './assumptions';
import { buildEstimateSensitivity } from './sensitivity';
import { adaptEstimateResponse, createMockEstimate, defaultInputs, toEstimateRequest } from '../model';

// Explicit integration run: HEADROOM_API_URL=http://127.0.0.1:8000 npm test -- src/api/live-contract.test.ts
// Normal unit tests need no listening server. Use the Vite URL to also test its proxy.
const baseUrl = process.env.HEADROOM_API_URL;
const baseline = toEstimateRequest(defaultInputs);
const cases = [
  baseline,
  { ...baseline, site_exposure: 0 },
  { ...baseline, flexibility_split: 0 },
  { ...baseline, site_exposure: 0.55 },
  { ...baseline, site_exposure: 0.9 },
  { ...baseline, term_years: 1, load_mw: 250 },
  { ...baseline, location_id: 'spp-oklahoma-city-demo', term_years: 3 },
  { ...baseline, location_id: 'spp-lincoln-demo', flexibility_split: 1, site_exposure: 1 },
];

describe.skipIf(!baseUrl)('live FastAPI / frontend contract parity', () => {
  it('loads real zone IDs and the three named scenarios from the running API', async () => {
    const locations = await getLocations({
      fetchImpl: ((input, init) => fetch(new URL(new URL(String(input), 'http://127.0.0.1:8000').pathname, baseUrl), init)) as typeof fetch,
      signal: AbortSignal.timeout(5000),
    });
    expect(locations.map(location => location.id)).toEqual(expect.arrayContaining([
      'CSWS', 'EDE', 'GRDA', 'INDN', 'KACY', 'KCPL', 'LES', 'MPS', 'NPPD',
      'OKGE', 'OPPD', 'SECI', 'SPP_SYSTEM', 'SPRM', 'SPS', 'WAUE', 'WFEC', 'WR',
      'spp-wichita-demo', 'spp-oklahoma-city-demo', 'spp-lincoln-demo',
    ]));
    expect(locations.filter(location => location.kind === 'scenario')).toHaveLength(3);
  });

  it.each(cases)('serves and adapts the current scenario: %j', async request => {
    const options = {
      fetchImpl: ((input, init) => fetch(new URL(new URL(String(input), 'http://127.0.0.1:8000').pathname, baseUrl), init)) as typeof fetch,
      signal: AbortSignal.timeout(5000),
    };
    const [response, assumptions] = await Promise.all([postEstimate(request, options), getEconomicsAssumptions(options)]);
    validateEconomicConsistency(response, assumptions);
    // A real reader may replace exposure independently of economics/evidence.
    // Only compare authored exposure when the backend explicitly says it is mock.
    if (response.modeled_exposure.source.ref.startsWith('mock://')) {
      expect(response.modeled_exposure.source.source_type).toBe('assumption');
      const expected = createMockEstimate(request).modeled_exposure;
      for (const quantile of ['p50', 'p90', 'p99'] as const) {
        expect(response.modeled_exposure[quantile]).toBeCloseTo(expected[quantile], 6);
      }
    }
    const result = adaptEstimateResponse(response);
    expect(result.annual_exposure.p50.value).toBe(response.modeled_exposure.p50);
    expect(result.annual_series).toHaveLength(request.term_years);
    expect(result.economics.annual_loss_usd.value).toBe(response.economics.annual_cost_usd.p50);
    const sensitivity = await buildEstimateSensitivity(response, assumptions, options);
    expect(sensitivity.baseline.decision).toBe(response.economics.decision);
    expect(sensitivity.baseline.net_value_usd.p50.value).toBe(response.economics.value_of_early_connection_usd - response.economics.annual_cost_usd.p50 * request.term_years);
    expect(sensitivity.rows.filter(row => row.status === 'not_modeled').map(row => row.key)).toEqual(['electricity_price', 'utilization']);
    for (const key of ['flexibility_split', 'site_exposure'] as const) {
      const row = sensitivity.rows.find(entry => entry.key === key)!;
      if (row.status !== 'modeled') throw new Error('Expected modeled sensitivity');
      for (const endpoint of [row.low, row.high]) {
        const changed = await postEstimate({ ...request, [key]: endpoint.input.value }, options);
        expect(endpoint.snapshot.decision).toBe(changed.economics.decision);
        expect(endpoint.snapshot.annual_cost_usd.p50.value).toBeCloseTo(changed.economics.annual_cost_usd.p50, 5);
      }
    }
  });
});
