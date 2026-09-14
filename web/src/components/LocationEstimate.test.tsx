// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../App';
import { locationFixture } from '../api/locationEstimator.fixture';

vi.mock('recharts', async importOriginal => ({ ...await importOriginal<typeof import('recharts')>(), ResponsiveContainer: () => <div /> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it('uses the existing controls, shows expected results, exports and restores location scenarios', async () => {
  vi.stubEnv('VITE_ESTIMATE_MODE', 'local');
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
  let exported: Blob | undefined;
  let release!: () => void;
  const released = new Promise<void>(resolve => { release = resolve; });
  vi.stubGlobal('URL', class extends URL { static createObjectURL(blob: Blob) { exported = blob; return 'blob:test'; } static revokeObjectURL() { release(); } });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string, options: RequestInit) => {
    const body = url.endsWith('/locations') ? { locations: ['Test, KS'] }
      : url.endsWith('/search') ? { status: 'succeeded', scan_id: 'scan-test', candidates: [{ name: 'Test City, Kansas', latitude: 38, longitude: -98 }] }
      : url.endsWith('/estimate') ? { status: 'succeeded', result: locationFixture(JSON.parse(String(options.body)).inputs) }
      : { detail: 'Rankings unavailable' };
    return new Response(JSON.stringify(body), { status: url.endsWith('/zone-rankings') ? 503 : 200 });
  }));
  try {
    render(<App />);
    fireEvent.change(screen.getByLabelText('SPP LOCATION'), { target: { value: 'custom-location' } });
    fireEvent.change(screen.getByLabelText('City, state or coordinates'), { target: { value: 'Test, KS' } });
    expect(screen.queryByText('Median scenario')).toBeNull();
    expect(screen.getAllByLabelText('LOAD SIZE')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Estimate location' }));
    await screen.findAllByText('Expected site exposure');
    expect(screen.getAllByText('Confidence: Low').length).toBeGreaterThan(0);
    expect(screen.queryByText('Upper-tail scenario')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Export scenario' }));
    const payload = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsText(exported!); });
    expect(JSON.parse(payload).response_origin).toBe('location_estimator');
    expect(JSON.parse(payload).result.exposure.regional_expected_hours).toBe(400);
    fireEvent.click(screen.getByRole('button', { name: /Save Active Scenario/ }));
    const ledger = screen.getByRole('region', { name: 'Scenario Comparison Ledger' });
    expect(within(ledger).getByText('Expected location exposure')).toBeDefined();
    expect(within(ledger).getAllByText('Unavailable')).toHaveLength(3);
    fireEvent.change(screen.getByLabelText('LOAD SIZE'), { target: { value: '250' } });
    expect(screen.queryAllByText('Expected site exposure')).toHaveLength(0);
    expect((screen.getByRole('button', { name: 'Exported' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(screen.getByText('Median scenario')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: /^Load inputs for Test City/ }));
    await waitFor(() => expect(screen.getAllByText('Expected site exposure')).toHaveLength(2));
    expect((screen.getByLabelText('City, state or coordinates') as HTMLInputElement).value).toBe('Test, KS');
  } finally { if (exported) await released; click.mockRestore(); }
}, 15000);
