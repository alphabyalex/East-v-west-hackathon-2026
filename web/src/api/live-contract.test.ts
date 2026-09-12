import { describe, expect, it } from 'vitest';
import { postEstimate } from './client';
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

function compare(actual: unknown, expected: unknown): void {
  if (typeof expected === 'number') {
    expect(actual).toBeCloseTo(expected, 6);
  } else if (Array.isArray(expected)) {
    expect(Array.isArray(actual)).toBe(true);
    expect(actual).toHaveLength(expected.length);
    expected.forEach((value, index) => compare((actual as unknown[])[index], value));
  } else if (expected && typeof expected === 'object') {
    expect(Object.keys(actual as object).sort()).toEqual(Object.keys(expected).sort());
    for (const [key, value] of Object.entries(expected)) {
      const supplied = (actual as Record<string, unknown>)[key];
      if (key === 'ref') expect(supplied).toMatch(/^mock:\/\//);
      else compare(supplied, value);
    }
  } else expect(actual).toEqual(expected);
}

describe.skipIf(!baseUrl)('live FastAPI / frontend contract parity', () => {
  it.each(cases)('serves and adapts the current scenario: %j', async request => {
    const response = await postEstimate(request, {
      fetchImpl: (input, init) => fetch(new URL(String(input), baseUrl), init),
      signal: AbortSignal.timeout(5000),
    });
    compare(response, createMockEstimate(request));
    const result = adaptEstimateResponse(response);
    expect(result.annual_exposure.p50.value).toBe(response.modeled_exposure.p50);
    expect(result.annual_series).toHaveLength(request.term_years);
    expect(result.economics.annual_loss_usd.value).toBe(response.economics.annual_cost_usd.p50);
  });
});
