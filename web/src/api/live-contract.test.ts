import { describe, expect, it } from 'vitest';
import { postEstimate } from './client';
import { getEconomicsAssumptions, validateEconomicConsistency } from './assumptions';
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
  });
});
