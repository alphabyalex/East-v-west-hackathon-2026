// @vitest-environment jsdom
import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, within, waitFor, configure } from '@testing-library/react';
import { cloneElement, type ReactElement } from 'react';
import App from './App';
import { createMockEstimate } from './model';
import { withEconomics } from './api/test-fixtures';
import { createLocationPreview } from './model/location-preview';
import { offlineLocations } from './api/locations';

// jsdom has no layout engine. Retain the real Recharts SVG/axes/tooltip components
// while giving their responsive wrapper a deterministic layout for interaction tests.
vi.mock('recharts', async importOriginal => {
  const real = await importOriginal<typeof import('recharts')>();
  return { ...real, ResponsiveContainer: ({ children }: { children: ReactElement<{ width: number; height: number }> }) => cloneElement(children, { width: 900, height: 250 }) };
});

beforeAll(() => {
  // Mounting the full instrument tab plus the debounced provider can exceed
  // Testing Library's one-second default on a busy workstation. Keep assertions
  // on the completed state; production request timing is unchanged.
  configure({ asyncUtilTimeout: 5000 });
  Object.defineProperty(window, 'matchMedia', { writable: true, value: (query: string) => ({ matches: query.includes('prefers-reduced-motion'), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
});
beforeEach(() => {
  window.history.replaceState({}, '', '/app');
  localStorage.clear();
  sessionStorage.clear();
  vi.stubEnv('VITE_ESTIMATE_MODE', 'local');
  // Companion panels still load in local mode; keep these tests off the live network.
  vi.stubGlobal('fetch', withEconomics(vi.fn().mockRejectedValue(new Error('Unexpected estimate request'))));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe('scenario workspace interactions', () => {
  it('keeps the overview as a standalone landing page before entering the workspace', () => {
    window.history.replaceState({}, '', '/');
    render(<App />);
    expect(screen.getByRole('heading', { name: /The contract won’t tell you.*when they’ll cut your power\./ })).toBeTruthy();
    expect(screen.queryByRole('tablist', { name: 'Workspace sections' })).toBeNull();
    expect(screen.getAllByRole('link', { name: 'Run your scenario' })).toHaveLength(3);
    expect(screen.getAllByRole('link', { name: 'Run your scenario' })[1].getAttribute('href')).toBe('/app');
  });

  it('opens on Scenario Stress Test and mounts only the selected tab without changing the URL', async () => {
    const errors = vi.spyOn(console, 'error');
    const requests = vi.spyOn(globalThis, 'fetch');
    const url = window.location.href;
    render(<App />);
    expect(screen.getByRole('tab', { name: 'Scenario Stress Test' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByRole('tab', { name: 'Overview' })).toBeNull();
    expect(screen.queryByText('NO LIVE GRID FETCHES')).toBeNull();
    expect(screen.queryByText(/Every number has a source\. Click/)).toBeNull();
    expect(requests.mock.calls.some(([url]) => String(url).endsWith('/api/zone-rankings'))).toBe(false);
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    expect((screen.getByRole('slider', { name: 'Site exposure factor' }) as HTMLInputElement).value).toBe('0.4');
    fireEvent.change(screen.getByLabelText('VPP ORCHESTRATION'), { target: { value: '500' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Zone Analytics' }));
    expect(await screen.findByRole('region', { name: 'Optimal Zones for Flexible Computes' })).toBeTruthy();
    expect(screen.queryByLabelText('SPP LOCATION')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Scenario Comparison Ledger' })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Portfolio & Alerts' }));
    expect(screen.getByRole('region', { name: 'Site portfolio' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Optimal Zones for Flexible Computes' })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    expect((screen.getByLabelText('VPP ORCHESTRATION') as HTMLInputElement).value).toBe('500');
    expect(screen.queryByRole('button', { name: 'Save Active Scenario' })).toBeNull();
    expect(document.querySelectorAll('[data-sensitivity-bar]')).toHaveLength(3);
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
    expect(screen.queryByRole('tab', { name: 'Overview' })).toBeNull();
    expect(window.location.href).toBe(url);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it('starts at Select zone without aggregate results or requests and returns there on reset', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    const requests = vi.spyOn(globalThis, 'fetch');
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    const dropdown = screen.getByLabelText('SPP LOCATION') as HTMLSelectElement;
    expect(dropdown.value).toBe('');
    expect(screen.getByRole('option', { name: 'Select zone' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'SPP system aggregate' })).toBeNull();
    expect(screen.queryByText('Median scenario')).toBeNull();
    expect((screen.getByRole('button', { name: 'Export scenario' }) as HTMLButtonElement).disabled).toBe(true);
    await new Promise(resolve => setTimeout(resolve, 100));
    expect(requests.mock.calls.some(([url]) => /api\/(estimate|grid-impact)/.test(String(url)))).toBe(false);
    fireEvent.change(dropdown, { target: { value: 'spp-wichita-demo' } });
    expect(screen.getByText('Median scenario')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(dropdown.value).toBe('');
    expect(screen.queryByText('Median scenario')).toBeNull();
    expect(screen.getByText('Select a zone to start your scenario.')).toBeTruthy();
  });

  it('supports keyboard navigation through the top tab strip', () => {
    render(<App />);
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Scenario Stress Test' }), { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Zone Analytics' }));
    expect(screen.getByRole('heading', { name: 'Read the signal before ranking the zones.' })).toBeTruthy();
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Portfolio & Alerts' }));
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Portfolio & Alerts' }));
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
  });

  it.each([1, 50, 500, 10000])('keeps the scenario and current sensitivity visible with %s VPP homes', homes => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    const field = screen.getByRole('spinbutton', { name: 'VPP ORCHESTRATION' });
    expect(() => fireEvent.change(field, { target: { value: String(homes) } })).not.toThrow();
    expect(screen.getByText('Connection economics')).toBeTruthy();
    expect(screen.getByText('VPP arbitrage revenue')).toBeTruthy();
    expect(document.querySelectorAll('[data-sensitivity-bar]').length).toBe(3);
    expect(screen.queryByText('Analysis could not be displayed')).toBeNull();
    expect((field as HTMLInputElement).value).toBe(String(homes));
  });

  it('renders the complete results view for a newly cataloged zone while its estimate is unavailable', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    const post = vi.fn().mockImplementation((_url, options) => {
      const request = JSON.parse(options.body);
      return Promise.resolve(request.location_id === 'CSWS'
        ? new Response('{}', { status: 404 })
        : new Response(JSON.stringify(createLocationPreview(request)), { status: 200 }));
    });
    const supplied = withEconomics(post);
    vi.stubGlobal('fetch', (url: RequestInfo | URL, options?: RequestInit) => String(url).endsWith('/api/locations')
      ? Promise.resolve(new Response(JSON.stringify({ locations: [
        ...offlineLocations, { id: 'CSWS', label: 'CSWS · SPP load zone', kind: 'zone' },
      ] }), { status: 200 }))
      : supplied(url, options));
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    await screen.findByRole('option', { name: 'CSWS · SPP load zone' });
    fireEvent.change(screen.getByRole('combobox', { name: 'SPP LOCATION' }), { target: { value: 'CSWS' } });
    expect(screen.getByText('SPP load zone · zone-specific model data; site exposure is your assumption')).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Retry estimate' })).toBeTruthy();
    expect((screen.getByRole('combobox', { name: 'SPP LOCATION' }) as HTMLSelectElement).value).toBe('CSWS');
    expect(post.mock.calls.some(([, options]) => JSON.parse(options.body).location_id === 'CSWS')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect annual values' }));
    const table = screen.getByRole('table', { name: /Sourced annual modeled exposure/ });
    expect(within(table).getAllByRole('row')).toHaveLength(8);
    fireEvent.click(within(table).getAllByRole('cell')[1].querySelector('button')!);
    const source = JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!);
    expect(source.source_type).toBe('assumption');
    expect(source.ref).toContain('placeholder preview for requested_location_id=CSWS');
  });

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
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
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

  it('removes repeated source badges while keeping section status and the site caveats', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    expect(screen.queryByText('ILLUSTRATIVE DATA')).toBeNull();
    expect(screen.queryByText('MOCK ECONOMICS')).toBeNull();
    expect(screen.queryByText('Assumed exposure')).toBeNull();
    expect(screen.queryByText('Assumed economics')).toBeNull();
    expect(screen.queryByText('Assumed decision')).toBeNull();
    expect(screen.queryByText('USER ASSUMPTION')).toBeNull();
    expect(document.querySelector('.mock-label, .assumption-badge, .source-mark, .lucide-info')).toBeNull();
    expect(screen.getByText('Scenario values · fitted model output unavailable')).toBeTruthy();
    expect(screen.getByText('GPU-hours, dollars and break-even use unverified scenario inputs.')).toBeTruthy();
    expect(screen.getByText(/The confidence signal has not been computed by an ensemble/)).toBeTruthy();
    expect(screen.getByRole('slider', { name: 'Site exposure factor' }).getAttribute('aria-valuetext')).toContain('user-set assumption');
    expect(screen.getByText('Scenario location · no site-specific grid data')).toBeTruthy();
    expect(screen.getByText('You set the mapping.')).toBeTruthy();
    const locations = screen.getByRole('combobox', { name: 'SPP LOCATION' });
    expect(locations.textContent).not.toContain('illustrative');
    expect(within(locations).getByRole('option', { name: 'Wichita, KS · scenario' }).getAttribute('value')).toBe('spp-wichita-demo');
  });

  it('keeps each section status aligned with mixed-source HTTP evidence without badges', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    vi.stubGlobal('fetch', withEconomics(vi.fn().mockImplementation((_url, options) => {
      const response = createMockEstimate(JSON.parse(options.body));
      // Test-only supplied references: exposure can arrive before economics/evidence.
      response.modeled_exposure.source = { source_type: 'model', ref: 'test-fixture://pipeline/exposure' };
      response.confidence.source = { source_type: 'model', ref: 'test-fixture://pipeline/confidence' };
      return Promise.resolve(new Response(JSON.stringify(response), { status: 200 }));
    })));
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    await screen.findByText(/Estimate service connected · current inputs synchronized/);
    expect(screen.queryByText('Assumed exposure')).toBeNull();
    expect(screen.queryByText('Assumed quantiles')).toBeNull();
    expect(screen.queryByRole('button', { name: /Assumed estimate confidence/ })).toBeNull();
    expect(screen.queryByText('Scenario values · fitted model output unavailable')).toBeNull();
    expect(screen.queryByText(/The confidence signal has not been computed by an ensemble/)).toBeNull();
    expect(screen.getByText('GPU-hours, dollars and break-even use unverified scenario inputs.')).toBeTruthy();
    expect(document.querySelector('.mock-label, .assumption-badge, .source-wrap .source-mark')).toBeNull();
    expect(screen.getByText('You set the mapping.')).toBeTruthy();
    expect(screen.getByText('Scenario location · no site-specific grid data')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Model & evidence' }));
    expect(screen.getByText('Evaluation unavailable.')).toBeTruthy();
    expect(screen.getByText('Tariff evidence not supplied')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: /model provenance/ })[0]);
    expect(JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!).ref).toContain('test-fixture://pipeline/exposure');
  });

  it('retains mixed-source crossover provenance without repeating the assumption caption', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    vi.stubGlobal('fetch', withEconomics(vi.fn().mockImplementation((_url, options) => {
      const response = createMockEstimate(JSON.parse(options.body));
      response.economics.source = { source_type: 'data', ref: 'test-fixture://sourced-economics' };
      return Promise.resolve(new Response(JSON.stringify(response), { status: 200 }));
    })));
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    await screen.findByText(/Estimate service connected · current inputs synchronized/);
    expect(screen.queryByText('Assumed economics')).toBeNull();
    expect(screen.queryByText('Assumed decision')).toBeNull();
    expect(screen.queryByText('GPU-hours, dollars and break-even use unverified scenario inputs.')).toBeNull();
    expect(screen.getByText('Scenario values · fitted model output unavailable')).toBeTruthy();
    const crossover = document.getElementById('exposure-explanation')!;
    expect(within(crossover).queryByText('Assumed')).toBeNull();
    expect(crossover.textContent).not.toContain('under these assumptions');
    expect(screen.getByText('You set the mapping.')).toBeTruthy();
    fireEvent.click(within(crossover).getByRole('button'));
    const source = JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!);
    expect(source.ref).toContain('test-fixture://sourced-economics');
    expect(source.ref).toContain('exposure_baseline_source=mock://');
  });

  it('opens the inline transparency panel and restores trigger focus on Escape', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
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

  it('makes HTTP fallback and successful retry visible', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    let rejectOnce = true;
    const fetcher = vi.fn().mockImplementation((_url, options) => {
      if (rejectOnce) {
        rejectOnce = false;
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return Promise.resolve(new Response(JSON.stringify(createMockEstimate(JSON.parse(options.body))), { status: 200 }));
    });
    vi.stubGlobal('fetch', withEconomics(fetcher));
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    expect(screen.getByText(/current inputs shown as assumed scenario values/)).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry estimate' }));
    expect(await screen.findByText(/Estimate service connected · current inputs synchronized/)).toBeTruthy();
  });

  it('makes economic overrides explicit and restores supplied defaults', async () => {
    vi.stubEnv('VITE_ESTIMATE_MODE', 'api');
    const fetcher = vi.fn().mockImplementation((_url, options) => Promise.resolve(new Response(JSON.stringify(createMockEstimate(JSON.parse(options.body))), { status: 200 })));
    vi.stubGlobal('fetch', withEconomics(fetcher));
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    expect(await screen.findByText(/Estimate service connected · current inputs synchronized/)).toBeTruthy();
    const gpuValue = screen.getByRole('spinbutton', { name: 'LOST COMPUTE VALUE' });
    fireEvent.change(gpuValue, { target: { value: '5' } });
    expect(screen.getByRole('button', { name: 'Assumed scenario' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText(/Economic input changed/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Use supplied defaults' }));
    expect((gpuValue as HTMLInputElement).value).toBe('3');
    expect(await screen.findByText(/current inputs synchronized/i)).toBeTruthy();
  });

  it('starts with the surface and allows switching to the fan chart', async () => {
    const renderer = await import('./components/exposure-surface/renderer');
    vi.stubGlobal('WebGL2RenderingContext', class {});
    const controller = { update: vi.fn(), updateGrid: vi.fn(), select: vi.fn(), reset: vi.fn(), rotate: vi.fn(), zoom: vi.fn(), dispose: vi.fn() };
    vi.spyOn(renderer, 'createSurfaceRenderer').mockReturnValue(controller);
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    expect(await screen.findByRole('group', { name: 'Interactive modeled exposure quantile surface' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Surface' }).getAttribute('aria-pressed')).toBe('true');
    expect(controller.update).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Fan chart' }));
    expect(screen.getByRole('button', { name: 'Fan chart' }).getAttribute('aria-pressed')).toBe('true');
    expect(controller.dispose).toHaveBeenCalled();
  });

  it('falls back without WebGL and keeps the upper-tail source available after recomputation', async () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
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
    const provenance = JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!);
    expect(provenance.value).toBe(0);
    expect(provenance.source_type).toBe('assumption');
    expect(provenance.ref).toContain('p99');
    expect(provenance.ref).toContain('site_exposure=0');
  });

  it('moves through every decision state and resets the scenario', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    const slider = screen.getByRole('slider', { name: 'Site exposure factor' });
    expect(screen.getByRole('heading', { name: 'worth it' })).toBeTruthy();
    fireEvent.change(slider, { target: { value: '0.55' } });
    expect(screen.getByRole('heading', { name: 'close call' })).toBeTruthy();
    fireEvent.change(slider, { target: { value: '0.9' } });
    expect(screen.getByRole('heading', { name: 'not worth it' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect((slider as HTMLInputElement).value).toBe('0.4');
    expect((screen.getByLabelText('SPP LOCATION') as HTMLSelectElement).value).toBe('');
    expect(screen.queryByRole('heading', { name: 'worth it' })).toBeNull();
  });

  it('normalizes cleared and out-of-range fields on blur and handles zero interruption cost', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    const load = screen.getByRole('spinbutton', { name: 'LOAD SIZE' }) as HTMLInputElement;
    fireEvent.change(load, { target: { value: '' } });
    fireEvent.blur(load);
    expect(load.value).toBe('100');
    fireEvent.change(load, { target: { value: '99999' } });
    expect(JSON.parse(load.getAttribute('data-provenance')!).ref).toContain('unapplied-draft');
    expect(load.title).toBe('');
    fireEvent.blur(load);
    expect(load.value).toBe('2000');
    const split = screen.getByRole('spinbutton', { name: 'FLEXIBILITY SPLIT' });
    fireEvent.change(split, { target: { value: '0' } });
    expect(screen.getByText('No modeled cost')).toBeTruthy();
    expect(screen.getByText('No cost crossover within this slider range')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/NaN|Infinity/);
  });

  it('keeps exact source metadata with intentional inspection and Escape dismissal, without a raw JSON box', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    const input = screen.getByRole('spinbutton', { name: 'LOAD SIZE' });
    fireEvent.change(input, { target: { value: '120' } });
    expect(screen.queryByRole('button', { name: /LOAD SIZE provenance/ })).toBeNull();
    expect(JSON.parse(input.getAttribute('data-provenance')!)).toEqual({ value: 120, source_type: 'assumption', ref: 'user://scenario/load_mw' });
    for (const field of document.querySelectorAll('.number-field, .location-field, .slider-readout, .slider-endpoints')) {
      expect(field.querySelector('.source-wrap, .source-info')).toBeNull();
      fireEvent.click(field);
      expect(screen.queryByRole('tooltip')).toBeNull();
    }
    const source = document.querySelector<HTMLButtonElement>('.source-wrap')!;
    expect(source.textContent).not.toBe('');
    expect(source.querySelector('svg')).toBeNull();
    fireEvent.mouseEnter(source);
    fireEvent.focus(source);
    expect(screen.queryByRole('tooltip')).toBeNull();
    const expected = JSON.parse(source.getAttribute('data-provenance')!);
    fireEvent.click(source);
    const tooltip = screen.getByRole('tooltip');
    const provenance = JSON.parse(tooltip.getAttribute('data-provenance')!);
    expect(provenance).toEqual(expected);
    expect(tooltip.querySelector('.provenance-heading')?.textContent).toBe('Source details');
    expect(tooltip.textContent).toContain(expected.source_type);
    expect(tooltip.querySelector('pre')).toBeNull();
    expect(screen.queryByText(/Activate the value or its source tag to pin/)).toBeNull();
    expect(source.getAttribute('aria-pressed')).toBe('true');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.click(source);
    expect(screen.getByRole('tooltip')).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(source.getAttribute('aria-pressed')).toBe('false');
  });

  it('keeps sourced annual values synchronized with the horizon, location, and slider', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
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

  it('toggles and displays detailed climate telemetry and schematic for SPP selections', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    
    // Telemetry drawer should be closed by default
    expect(screen.queryByRole('region', { name: 'Climate telemetry nodes' })).toBeNull();
    
    const toggle = screen.getByRole('button', { name: 'Inspect climate telemetry' });
    expect(toggle).toBeTruthy();
    
    // Toggle to open the drawer
    fireEvent.click(toggle);
    expect(toggle.textContent).toContain('Hide telemetry');
    const drawer = screen.getByRole('region', { name: 'Climate telemetry nodes' });
    expect(drawer).toBeTruthy();
    
    // Explicitly selected Wichita exposes its own station.
    expect(within(drawer).getByText('KICT')).toBeTruthy();
    expect(within(drawer).getByText(/Model Climate Input/)).toBeTruthy();

    // Assert Sourced coordinates button is clickable and exposes provenance
    const coordsBtn = within(drawer).getByRole('button', { name: /37.69° N, 97.34° W. data provenance/ });
    expect(coordsBtn).toBeTruthy();
    fireEvent.click(coordsBtn);
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toBeTruthy();
    const provenance = JSON.parse(tooltip.getAttribute('data-provenance')!);
    expect(provenance.source_type).toBe('data');
    expect(provenance.ref).toContain('mock://weather-telemetry/station/ict');
    
    // Close the tooltip
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
    
    // Select different location and verify stations list and diagram updates
    fireEvent.change(screen.getByRole('combobox', { name: 'SPP LOCATION' }), { target: { value: 'spp-wichita-demo' } });
    expect(within(drawer).getByText('KICT')).toBeTruthy();
    expect(within(drawer).queryByText('KOKC')).toBeNull(); // KOKC should not be rendered for Wichita demo
    
    const updatedAscii = within(drawer).getByText(/Model Climate Input/);
    expect(updatedAscii).toBeTruthy();
    expect(updatedAscii.textContent).toContain('KICT: Eisenhower National Airport');
    
    // Toggle again to close the drawer
    fireEvent.click(toggle);
    expect(toggle.textContent).toContain('Inspect climate telemetry');
    expect(screen.queryByRole('region', { name: 'Climate telemetry nodes' })).toBeNull();
  });

  it('has one portfolio save entry point in the scenario header and no local ledger', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('tab', { name: 'Scenario Stress Test' }));
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'spp-wichita-demo' } });
    expect(document.querySelector('.workspace-actions')?.contains(screen.getByRole('button', { name: 'Save to Portfolio' }))).toBe(true);
    expect(screen.queryByRole('button', { name: 'Save Active Scenario' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Scenario Comparison Ledger' })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Portfolio & Alerts' }));
    expect(screen.queryByRole('button', { name: 'Save current site to portfolio' })).toBeNull();
    expect(screen.queryByLabelText('Portfolio site name')).toBeNull();
  });
});
