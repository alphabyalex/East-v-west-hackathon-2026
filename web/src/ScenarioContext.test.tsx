// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { ScenarioProvider, useScenario } from './ScenarioContext';
import { createMockEstimate, defaultInputs, deriveScenario, toEstimateRequest, type EstimateRequest, type EstimateResponse } from './model';
import type { EstimateMode } from './hooks/useEstimateTransport';

function setup(mode?: EstimateMode) {
  return renderHook(() => useScenario(), {
    wrapper: ({ children }: { children: ReactNode }) => <ScenarioProvider initialMode={mode}>{children}</ScenarioProvider>,
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const jsonResponse = (body: EstimateResponse) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const initialRequest = toEstimateRequest(defaultInputs);
async function tick(ms = 50) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('scenario HTTP provider', () => {
  it('defaults to the API, posts exactly the canonical fields, and consumes the server response without rescaling', async () => {
    const returned = createMockEstimate(initialRequest);
    returned.confidence = { level: 'High', score: 0.8, basis: 'test_server_response', source: { source_type: 'model', ref: 'test://server' } };
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(returned));
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup();
    expect(result.current.status).toBe('loading');
    expect(result.current.result.confidence.level).toBe('Medium');
    await tick();
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe('/api/estimate');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual(initialRequest);
    expect(result.current.status).toBe('api');
    expect(result.current.result.canonical_response).toEqual(returned);
    expect(result.current.result.annual_exposure.p50.value).toBe(returned.modeled_exposure.p50);
    expect(result.current.result.confidence.level).toBe('High');
  });

  it('uses the local env flag with instantaneous recomputation and no fetch', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'local');
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup();
    act(() => result.current.update('site_exposure', 0.8));
    expect(result.current.mode).toBe('local');
    expect(result.current.status).toBe('local');
    expect(result.current.result.canonical_response.inputs_echo.site_exposure).toBe(0.8);
    await tick(10_000);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('coalesces rapid slider changes into one request while previewing every current input', async () => {
    const fetcher = vi.fn().mockImplementation((_url, options) => Promise.resolve(jsonResponse(createMockEstimate(JSON.parse(options.body)))));
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup('api');
    act(() => result.current.update('site_exposure', 0.6));
    await tick(20);
    act(() => result.current.update('site_exposure', 0.9));
    expect(result.current.result.canonical_response.inputs_echo.site_exposure).toBe(0.9);
    expect(result.current.status).toBe('loading');
    await tick();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1].body).site_exposure).toBe(0.9);
    expect(result.current.status).toBe('api');
  });

  it('replaces a previous API result immediately with a matching local preview when inputs change', async () => {
    const next = deferred<Response>();
    const serverResponse = createMockEstimate(initialRequest);
    serverResponse.confidence.level = 'High';
    const fetcher = vi.fn().mockResolvedValueOnce(jsonResponse(serverResponse)).mockReturnValueOnce(next.promise);
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup('api');
    await tick();
    expect(result.current.result.confidence.level).toBe('High');
    act(() => result.current.update('site_exposure', 0.7));
    expect(result.current.status).toBe('loading');
    expect(result.current.result.confidence.level).toBe('Medium');
    expect(result.current.result.annual_exposure.p50.value).toBe(deriveScenario({ ...defaultInputs, site_exposure: 0.7 }).annual_exposure.p50.value);
    expect(result.current.result.canonical_response.inputs_echo.site_exposure).toBe(0.7);
    await tick();
    await act(async () => { next.resolve(jsonResponse(createMockEstimate({ ...initialRequest, site_exposure: 0.7 }))); });
    expect(result.current.status).toBe('api');
  });

  it('aborts superseded requests and ignores their late out-of-order responses', async () => {
    const old = deferred<Response>();
    const latest = deferred<Response>();
    const fetcher = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup('api');
    await tick();
    const oldSignal = fetcher.mock.calls[0][1].signal as AbortSignal;
    act(() => result.current.update('load_mw', 250));
    expect(oldSignal.aborted).toBe(true);
    await tick();
    const current = createMockEstimate({ ...initialRequest, load_mw: 250 });
    await act(async () => { latest.resolve(jsonResponse(current)); });
    await act(async () => { old.resolve(jsonResponse(createMockEstimate(initialRequest))); });
    expect(result.current.status).toBe('api');
    expect(result.current.result.canonical_response.inputs_echo.load_mw).toBe(250);
  });

  it('bounds a hung request, aborts it, and retains current local results even if it eventually resolves', async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn().mockReturnValue(pending.promise);
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup('api');
    await tick();
    await tick(3000);
    expect(result.current.status).toBe('fallback');
    expect(result.current.error).toContain('timed out');
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    await act(async () => { pending.resolve(jsonResponse(createMockEstimate(initialRequest))); });
    expect(result.current.status).toBe('fallback');
    expect(result.current.result.canonical_response.inputs_echo).toEqual(initialRequest);
  });

  it('shows a visible fallback after network failure and retries the current inputs', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce(jsonResponse(createMockEstimate(initialRequest)));
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup('api');
    await tick();
    expect(result.current.status).toBe('fallback');
    expect(result.current.error).toContain('local mock');
    act(() => result.current.retry());
    expect(result.current.status).toBe('loading');
    await tick();
    expect(result.current.status).toBe('api');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('falls back for malformed or stale server echoes instead of displaying them', async () => {
    const stale = createMockEstimate({ ...initialRequest, site_exposure: 0.1 });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(stale)));
    const { result } = setup('api');
    await tick();
    expect(result.current.status).toBe('fallback');
    expect(result.current.result.canonical_response.inputs_echo.site_exposure).toBe(initialRequest.site_exposure);
  });

  it('switches to local mode for an economic override and explicitly resets it when API defaults are selected', async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn().mockReturnValue(pending.promise);
    vi.stubGlobal('fetch', fetcher);
    const { result } = setup('api');
    await tick();
    act(() => result.current.update('gpu_hour_value_usd', 5));
    expect(result.current.mode).toBe('local');
    expect(result.current.status).toBe('local');
    expect(result.current.modeNote).toContain('Economic input changed');
    expect(result.current.inputs.gpu_hour_value_usd).toBe(5);
    expect(result.current.result.economics.annual_loss_usd.value).toBe(deriveScenario({ ...defaultInputs, gpu_hour_value_usd: 5 }).economics.annual_loss_usd.value);
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    await tick(5000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    act(() => result.current.chooseMode('api'));
    expect(result.current.inputs.gpu_hour_value_usd).toBe(defaultInputs.gpu_hour_value_usd);
    expect(result.current.sourceFor('gpu_hour_value_usd').ref).toContain('docs/ASSUMPTIONS.md');
    expect(result.current.modeNote).toContain('reset');
    expect(result.current.status).toBe('loading');
    await tick();
    const request = JSON.parse(fetcher.mock.calls[1][1].body) as EstimateRequest;
    expect(request).toEqual(initialRequest);
    expect(Object.keys(request)).toHaveLength(5);
  });

  it('does not leave API mode for an unchanged economic value, and aborts on unmount', async () => {
    const fetcher = vi.fn().mockReturnValue(new Promise(() => {}));
    vi.stubGlobal('fetch', fetcher);
    const { result, unmount } = setup('api');
    act(() => result.current.update('gpu_per_mw', defaultInputs.gpu_per_mw));
    expect(result.current.mode).toBe('api');
    await tick();
    const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    await tick(4000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
