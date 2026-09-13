// @vitest-environment jsdom
import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, within } from '@testing-library/react';
import { cloneElement, type ReactElement } from 'react';
import App from './App';
import { createMockEstimate } from './model';
import { withEconomics } from './api/test-fixtures';

// jsdom has no layout engine. Retain the real Recharts SVG/axes/tooltip components
// while giving their responsive wrapper a deterministic layout for interaction tests.
vi.mock('recharts', async importOriginal => {
  const real = await importOriginal<typeof import('recharts')>();
  return { ...real, ResponsiveContainer: ({ children }: { children: ReactElement<{ width: number; height: number }> }) => cloneElement(children, { width: 900, height: 250 }) };
});

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', { writable: true, value: (query: string) => ({ matches: query.includes('prefers-reduced-motion'), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
});
beforeEach(() => vi.stubEnv('VITE_ESTIMATE_MODE', 'local'));
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe('scenario workspace interactions', () => {
  it('exports the current sourced sensitivity without extending the canonical API response', async () => {
    let exported: Blob | undefined;
    let release!: () => void;
    const released = new Promise<void>(resolve => { release = resolve; });
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL(blob: Blob) { exported = blob; return 'blob:scenario-test'; }
      static revokeObjectURL() { release(); }
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      render(<App />);
      fireEvent.change(screen.getByRole('slider', { name: 'Site exposure factor' }), { target: { value: '0' } });
      fireEvent.click(screen.getByRole('button', { name: 'Export scenario' }));
      const json = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsText(exported!);
      });
      const payload = JSON.parse(json);
      expect(payload.request.site_exposure).toBe(0);
      expect(payload.response_origin).toBe('local_mock');
      expect(payload.response).not.toHaveProperty('sensitivity');
      expect(payload.sensitivity.rows.filter((row: { status: string }) => row.status === 'modeled')).toHaveLength(3);
      const electricity = payload.sensitivity.rows.find((row: { key: string }) => row.key === 'electricity_price');
      expect(electricity.low.snapshot).toBeNull();
      expect(electricity.swing_usd).toBeNull();
      expect(payload.sensitivity.baseline.net_value_usd.p50.value).toBe(payload.result.economics.net_value_usd.value);
      expect(payload.sensitivity.source.ref).toMatch(/^mock:/);
      await released; // Keep the URL stub until the export's delayed cleanup runs.
    } finally { click.mockRestore(); }
  });

  it('keeps mock status near results and preserves the permanent site caveats', () => {
    render(<App />);
    expect(screen.queryByText('ILLUSTRATIVE DATA')).toBeNull();
    expect(screen.queryByText('MOCK ECONOMICS')).toBeNull();
    expect(screen.getAllByText('Mock exposure')).toHaveLength(3);
    expect(screen.getByText('Mock economics')).toBeTruthy();
    expect(screen.getByText('Mock decision')).toBeTruthy();
    expect(screen.getByText('USER ASSUMPTION')).toBeTruthy();
    expect(screen.getByText('System aggregate · no site-specific grid data')).toBeTruthy();
    expect(screen.getByText('You set the mapping.')).toBeTruthy();
  });

  it('removes only earned mock labels when a mixed-source HTTP result arrives', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    vi.stubGlobal('fetch', withEconomics(vi.fn().mockImplementation((_url, options) => {
      const response = createMockEstimate(JSON.parse(options.body));
      // Test-only supplied references: exposure can arrive before economics/evidence.
      response.modeled_exposure.source = { source_type: 'model', ref: 'test-fixture://pipeline/exposure' };
      response.confidence.source = { source_type: 'model', ref: 'test-fixture://pipeline/confidence' };
      return Promise.resolve(new Response(JSON.stringify(response), { status: 200 }));
    })));
    render(<App />);
    await screen.findByText(/API connected · current inputs synchronized/);
    expect(screen.queryByText('Mock exposure')).toBeNull();
    expect(screen.queryByText('Mock quantiles')).toBeNull();
    expect(screen.queryByRole('button', { name: /Mock estimate confidence/ })).toBeNull();
    expect(screen.getByText('Mock economics')).toBeTruthy();
    expect(screen.getByText('Mock decision')).toBeTruthy();
    expect(screen.getByText('USER ASSUMPTION')).toBeTruthy();
    expect(screen.getByText('System aggregate · no site-specific grid data')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Model & evidence' }));
    expect(screen.getByText('Mock diagnostics · not computed.')).toBeTruthy();
    expect(screen.getByText('Mock clause · not extracted')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: /model provenance/ })[0]);
    expect(JSON.parse(screen.getByRole('tooltip').querySelector('pre')!.textContent!).ref).toContain('test-fixture://pipeline/exposure');
  });

  it('retains the crossover mock label if exposure is still mock after sourced economics arrive', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    vi.stubGlobal('fetch', withEconomics(vi.fn().mockImplementation((_url, options) => {
      const response = createMockEstimate(JSON.parse(options.body));
      response.economics.source = { source_type: 'data', ref: 'test-fixture://sourced-economics' };
      return Promise.resolve(new Response(JSON.stringify(response), { status: 200 }));
    })));
    render(<App />);
    await screen.findByText(/API connected · current inputs synchronized/);
    expect(screen.queryByText('Mock economics')).toBeNull();
    expect(screen.queryByText('Mock decision')).toBeNull();
    expect(screen.getAllByText('Mock exposure')).toHaveLength(3);
    const crossover = document.getElementById('exposure-explanation')!;
    expect(within(crossover).getByText('Mock')).toBeTruthy();
    fireEvent.click(within(crossover).getByRole('button'));
    const source = JSON.parse(screen.getByRole('tooltip').querySelector('pre')!.textContent!);
    expect(source.ref).toContain('test-fixture://sourced-economics');
    expect(source.ref).toContain('exposure_baseline_source=mock://');
  });

  it('opens the inline transparency panel and restores trigger focus on Escape', () => {
    render(<App />);
    const trigger = screen.getByRole('button', { name: 'Model & evidence' });
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    const panel = document.getElementById('transparency-panel')!;
    expect(panel).toBeTruthy();
    fireEvent.change(screen.getByRole('slider', { name: 'Site exposure factor' }), { target: { value: '0.65' } });
    expect(within(panel).getByRole('button', { name: /Site exposure factor assumption: 0.65/ })).toBeTruthy();
    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(document.getElementById('transparency-panel')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('makes HTTP fallback, retry, and the economic override mode switch visible', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')).mockImplementation((_url, options) => Promise.resolve(new Response(JSON.stringify(createMockEstimate(JSON.parse(options.body))), { status: 200 })));
    vi.stubGlobal('fetch', withEconomics(fetcher));
    render(<App />);
    expect(screen.getByText(/current inputs shown as a local mock preview/)).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry API' }));
    expect(await screen.findByText(/API connected · current inputs synchronized/)).toBeTruthy();
    const gpuValue = screen.getByRole('spinbutton', { name: 'LOST COMPUTE VALUE' });
    fireEvent.change(gpuValue, { target: { value: '5' } });
    expect(screen.getByRole('button', { name: 'Local mock' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText(/Economic input changed/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Use API defaults' }));
    expect((gpuValue as HTMLInputElement).value).toBe('3');
    expect(await screen.findByText(/API connected · current inputs synchronized/)).toBeTruthy();
  });

  it('starts with the fan chart without initializing a graphics context', () => {
    render(<App />);
    expect(screen.getByRole('button', { name: 'Fan chart' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByRole('group', { name: 'Interactive modeled exposure quantile surface' })).toBeNull();
    expect(document.querySelector('canvas')).toBeNull();
    expect(screen.queryByText(/Interactive surface unavailable/)).toBeNull();
  });

  it('falls back without WebGL and keeps the upper-tail source available after recomputation', async () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Surface' }));
    // The cold, lazy Three.js import can exceed RTL's one-second default when
    // the complete real-Recharts suite runs concurrently; keep a bounded wait.
    expect(await screen.findByText(/Interactive surface unavailable on this device/, {}, { timeout: 3500 })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Fan chart' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('group', { name: /Annual modeled exposure fan chart/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect annual values' }));
    const table = screen.getByRole('table', { name: /Sourced annual modeled exposure/ });
    expect(within(table).getAllByRole('columnheader')).toHaveLength(4);
    fireEvent.change(screen.getByRole('slider', { name: 'Site exposure factor' }), { target: { value: '0' } });
    const upperTail = within(table).getAllByRole('cell')[3];
    fireEvent.click(within(upperTail).getAllByRole('button')[0]);
    const provenance = JSON.parse(screen.getByRole('tooltip').querySelector('pre')!.textContent!);
    expect(provenance.value).toBe(0);
    expect(provenance.source_type).toBe('assumption');
    expect(provenance.ref).toContain('p99');
    expect(provenance.ref).toContain('site_exposure=0');
  });

  it('moves through every decision state and resets the scenario', () => {
    render(<App />);
    const slider = screen.getByRole('slider', { name: 'Site exposure factor' });
    expect(screen.getByRole('heading', { name: 'worth it' })).toBeTruthy();
    fireEvent.change(slider, { target: { value: '0.55' } });
    expect(screen.getByRole('heading', { name: 'close call' })).toBeTruthy();
    fireEvent.change(slider, { target: { value: '0.9' } });
    expect(screen.getByRole('heading', { name: 'not worth it' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect((slider as HTMLInputElement).value).toBe('0.4');
    expect(screen.getByRole('heading', { name: 'worth it' })).toBeTruthy();
  });

  it('normalizes cleared and out-of-range fields on blur and handles zero interruption cost', () => {
    render(<App />);
    const load = screen.getByRole('spinbutton', { name: 'LOAD SIZE' }) as HTMLInputElement;
    fireEvent.change(load, { target: { value: '' } });
    fireEvent.blur(load);
    expect(load.value).toBe('100');
    fireEvent.change(load, { target: { value: '99999' } });
    expect(JSON.parse(load.title).ref).toContain('unapplied-draft');
    fireEvent.blur(load);
    expect(load.value).toBe('2000');
    const split = screen.getByRole('spinbutton', { name: 'FLEXIBILITY SPLIT' });
    fireEvent.change(split, { target: { value: '0' } });
    expect(screen.getByText('No modeled cost')).toBeTruthy();
    expect(screen.getByText('No cost crossover within this slider range')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/NaN|Infinity/);
  });

  it('exposes the exact value and user-assumption provenance, with pin and Escape dismissal', () => {
    render(<App />);
    const input = screen.getByRole('spinbutton', { name: 'LOAD SIZE' });
    fireEvent.change(input, { target: { value: '120' } });
    const source = screen.getByRole('button', { name: /LOAD SIZE provenance/ });
    fireEvent.click(source);
    const tooltip = screen.getByRole('tooltip');
    const provenance = JSON.parse(tooltip.querySelector('pre')!.textContent!);
    expect(provenance).toEqual({ value: 120, source_type: 'assumption', ref: 'user://scenario/load_mw' });
    expect(source.getAttribute('aria-pressed')).toBe('true');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('keeps sourced annual values synchronized with the horizon, location, and slider', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect annual values' }));
    const table = screen.getByRole('table', { name: /Sourced annual modeled exposure/ });
    expect(within(table).getAllByRole('row')).toHaveLength(8);
    const originalFirstValue = within(table).getAllByRole('cell')[2].textContent;
    fireEvent.change(screen.getByRole('combobox', { name: 'SPP LOCATION' }), { target: { value: 'spp-oklahoma-city-demo' } });
    expect(within(table).getAllByRole('cell')[2].textContent).not.toBe(originalFirstValue);
    fireEvent.change(screen.getByRole('spinbutton', { name: 'CONTRACT TERM' }), { target: { value: '1' } });
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    fireEvent.change(screen.getByRole('slider', { name: 'Site exposure factor' }), { target: { value: '0' } });
    const cells = within(table).getAllByRole('cell');
    for (const cell of cells.slice(1)) expect(cell.querySelector('.sourced-value')!.textContent).toBe('0');
    expect(document.body.textContent).not.toMatch(/NaN|Infinity/);
  });
});
