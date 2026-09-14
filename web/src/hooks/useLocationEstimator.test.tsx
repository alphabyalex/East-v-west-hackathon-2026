// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useLocationEstimator } from './useLocationEstimator';
import { defaultInputs } from '../model';
import { locationFixture } from '../api/locationEstimator.fixture';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const single = { status: 'succeeded', scan_id: 'scan-test', candidates: [{ name: 'Test City, Kansas', latitude: 38, longitude: -98 }] };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('does no location work until requested, sends every input and invalidates changed inputs', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string, options: RequestInit) => {
    if (url.endsWith('/locations')) return json({ locations: [] });
    if (url.endsWith('/search')) return json(single);
    return json({ status: 'succeeded', result: locationFixture(JSON.parse(String(options.body)).inputs) });
  });
  vi.stubGlobal('fetch', fetcher);
  const { result, rerender } = renderHook(({ inputs }) => useLocationEstimator(inputs), { initialProps: { inputs: { ...defaultInputs } } });
  expect(fetcher).not.toHaveBeenCalled();
  act(() => { result.current.activate(true); result.current.changeQuery('Test, KS'); });
  await act(() => result.current.submit());
  expect(result.current.result?.exposure.annual_expected_hours).toBe(400 * defaultInputs.site_exposure);
  const post = fetcher.mock.calls.find(([url]) => url.endsWith('/estimate'))!;
  expect(JSON.parse(post[1].body)).toEqual({ scan_id: 'scan-test', candidate: 0, inputs: defaultInputs });
  rerender({ inputs: { ...defaultInputs, load_mw: 250 } });
  expect(result.current.result).toBeUndefined();
  expect(result.current.stale).toBe(true);
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/estimate'))).toHaveLength(1);
});

it('requires an explicit choice for ambiguous cities', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string, options: RequestInit) => {
    if (url.endsWith('/locations')) return json({ locations: [] });
    if (url.endsWith('/search')) return json({ ...single, candidates: [...single.candidates, { name: 'Another city', latitude: 39, longitude: -98 }] });
    return json({ status: 'succeeded', result: locationFixture(JSON.parse(String(options.body)).inputs) });
  });
  vi.stubGlobal('fetch', fetcher);
  const { result } = renderHook(() => useLocationEstimator(defaultInputs));
  act(() => { result.current.activate(true); result.current.changeQuery('Test'); });
  await act(() => result.current.submit());
  expect(result.current.phase).toBe('choosing');
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/estimate'))).toBe(false);
  await act(() => result.current.submit(1));
  expect(result.current.result).toBeDefined();
  expect(JSON.parse(fetcher.mock.calls.find(([url]) => url.endsWith('/estimate'))![1].body).candidate).toBe(1);
});

it('discards a late response after the query changes', async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/locations')) return json({ locations: [] });
    if (url.endsWith('/search')) return json(single);
    return new Promise<Response>(resolve => { finish = resolve; });
  });
  vi.stubGlobal('fetch', fetcher);
  const { result } = renderHook(() => useLocationEstimator(defaultInputs));
  act(() => { result.current.activate(true); result.current.changeQuery('First city'); });
  let task!: Promise<void>;
  act(() => { task = result.current.submit(); });
  await waitFor(() => expect(finish).toBeDefined());
  act(() => result.current.changeQuery('Second city'));
  await act(async () => { finish(json({ status: 'succeeded', result: locationFixture(defaultInputs) })); await task; });
  expect(result.current.result).toBeUndefined();
  expect(result.current.query).toBe('Second city');
});

it('polls an explicit job and reads that job result with the captured inputs', async () => {
  vi.useFakeTimers();
  let estimates = 0;
  const fetcher = vi.fn().mockImplementation(async (url: string, options: RequestInit) => {
    if (url.endsWith('/locations')) return json({ locations: [] });
    if (url.endsWith('/search')) return json(single);
    if (url.includes('/jobs/')) return json({ status: 'succeeded', result_id: 'completed-report' });
    if (++estimates === 1) return json({ status: 'running', job_id: 'my-job' });
    return json({ status: 'succeeded', result: locationFixture(JSON.parse(String(options.body)).inputs) });
  });
  vi.stubGlobal('fetch', fetcher);
  const { result } = renderHook(() => useLocationEstimator(defaultInputs));
  act(() => { result.current.activate(true); result.current.changeQuery('Test city'); });
  let task!: Promise<void>;
  await act(async () => { task = result.current.submit(); await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); await task; });
  expect(result.current.result).toBeDefined();
  const posts = fetcher.mock.calls.filter(([url]) => url.endsWith('/estimate'));
  expect(JSON.parse(posts[1][1].body)).toEqual({ scan_id: 'scan-test', candidate: 0, inputs: defaultInputs, report_id: 'completed-report' });
});

it('shows service errors without replacing them with scenario values', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/locations')
    ? json({ locations: [] }) : json({ detail: 'Location estimator unavailable.' }, 503)));
  const { result } = renderHook(() => useLocationEstimator(defaultInputs));
  act(() => { result.current.activate(true); result.current.changeQuery('Test city'); });
  await act(() => result.current.submit());
  expect(result.current.error).toBe('Location estimator unavailable.');
  expect(result.current.result).toBeUndefined();
});

it('rejects a result whose input echo differs from the current request', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/locations')) return json({ locations: [] });
    if (url.endsWith('/search')) return json(single);
    return json({ status: 'succeeded', result: locationFixture({ ...defaultInputs, site_exposure: .9 }) });
  }));
  const { result } = renderHook(() => useLocationEstimator(defaultInputs));
  act(() => { result.current.activate(true); result.current.changeQuery('Test city'); });
  await act(() => result.current.submit());
  expect(result.current.error).toMatch(/does not match/);
  expect(result.current.result).toBeUndefined();
});
