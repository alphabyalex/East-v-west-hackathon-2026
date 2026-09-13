// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import type { BaselineYear, SourcedValue } from '../model';
import ExposureSurface from './ExposureSurface';
import { createSurfaceRenderer, type SurfaceController } from './exposure-surface/renderer';

// Exercise the real controls and provenance portals independently of GPU support.
// The renderer's geometry, picking, and camera behavior have their own tests.
vi.mock('./exposure-surface/renderer', () => ({ createSurfaceRenderer: vi.fn() }));

const sourced = (value: number, ref: string): SourcedValue => ({ value, source_type: 'assumption', ref });
const row = (year: number, scale = 1): BaselineYear => ({
  year: sourced(year, `mock://test/year/${year}`),
  p50: sourced(40 * year * scale, `mock://test/year/${year}/p50?scale=${scale}`),
  p90: sourced(80 * year * scale, `mock://test/year/${year}/p90?scale=${scale}`),
  p99: sourced(123.456 * year * scale, `mock://test/year/${year}/p99?scale=${scale}`),
});
const rows = [row(1), row(2)];
const rendererMock = vi.mocked(createSurfaceRenderer);
let controller: SurfaceController;
let callbacks: Parameters<typeof createSurfaceRenderer>[1];

function readProvenance() {
  return JSON.parse(screen.getByRole('tooltip').querySelector('pre')!.textContent!);
}

beforeEach(() => {
  vi.stubGlobal('WebGL2RenderingContext', class {});
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('prefers-reduced-motion'), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  controller = { update: vi.fn(), select: vi.fn(), reset: vi.fn(), rotate: vi.fn(), zoom: vi.fn(), dispose: vi.fn() };
  rendererMock.mockReset();
  rendererMock.mockImplementation((_host, suppliedCallbacks) => { callbacks = suppliedCallbacks; return controller; });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('exposure surface inspection', () => {
  it('lets the user select a supplied year and percentile while preserving exact provenance behind rounded hours', () => {
    const confidence = { level: 'Medium' as const, score: sourced(0.5, 'mock://test/confidence/score'), basis: 'mock_ensemble_agreement', source: { source_type: 'assumption' as const, ref: 'mock://test/confidence/basis' } };
    render(<ExposureSurface rows={rows} maximumHours={300} confidence={confidence} onUnavailable={vi.fn()} />);
    expect(controller.select).toHaveBeenLastCalledWith(1);
    expect((screen.getByRole('button', { name: 'Inspect previous year' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Inspect p99' }));
    fireEvent.click(screen.getByRole('button', { name: 'Inspect next year' }));
    expect(controller.select).toHaveBeenLastCalledWith(5);
    expect(screen.getByRole('button', { name: 'Inspect p99' }).getAttribute('aria-pressed')).toBe('true');
    expect((screen.getByRole('button', { name: 'Inspect next year' }) as HTMLButtonElement).disabled).toBe(true);

    const value = screen.getByRole('button', { name: '246.9. assumption provenance. Activate to pin.' });
    expect(value.querySelector('.sourced-value')!.textContent).toBe('246.9');
    fireEvent.click(value);
    expect(readProvenance()).toEqual(rows[1].p99);
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: /p99 percentile definition:/ }));
    expect(readProvenance()).toEqual({ value: 99, source_type: 'assumption', ref: 'mock://display/surface/percentile/99; cumulative percentile, not probability density' });
    fireEvent.keyDown(document, { key: 'Escape' });
    const inspector = screen.getByLabelText('Selected supplied quantile');
    fireEvent.click(within(inspector).getByRole('button', { name: /Assumed estimate confidence: Medium/ }));
    expect(readProvenance()).toEqual(confidence.score);
  });

  it('reflects a picked vertex in the inspector and exposes sourced projected axis labels', () => {
    render(<ExposureSurface rows={rows} maximumHours={300} onUnavailable={vi.fn()} />);
    act(() => {
      callbacks.select(3);
      callbacks.labels([{ id: 'hours-300', kind: 'hours', datum: sourced(300, 'mock://test/display/axis-hours'), x: 20, y: 30, anchorX: 10, anchorY: 30 }]);
    });
    expect(screen.getByRole('button', { name: 'Inspect p50' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByRole('button', { name: 'Inspect p10' })).toBeNull();
    const inspector = screen.getByLabelText('Selected supplied quantile');
    fireEvent.click(within(inspector).getByRole('button', { name: '80. assumption provenance. Activate to pin.' }));
    expect(readProvenance()).toEqual(rows[1].p50);
    fireEvent.keyDown(document, { key: 'Escape' });
    const stage = screen.getByRole('group', { name: 'Interactive modeled exposure quantile surface' });
    fireEvent.click(within(stage).getByRole('button', { name: '300 h. assumption provenance. Activate to pin.' }));
    expect(readProvenance()).toEqual(sourced(300, 'mock://test/display/axis-hours'));
  });

  it('updates values and clamps the inspected year when the contract becomes a single year', () => {
    const onUnavailable = vi.fn();
    const { rerender, unmount } = render(<ExposureSurface rows={rows} maximumHours={300} onUnavailable={onUnavailable} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect next year' }));
    fireEvent.click(screen.getByRole('button', { name: 'Inspect p99' }));
    const changed = [row(1, 0.5)];
    rerender(<ExposureSurface rows={changed} maximumHours={300} onUnavailable={onUnavailable} />);
    expect(rendererMock).toHaveBeenCalledTimes(1);
    expect(controller.update).toHaveBeenLastCalledWith(changed, 300);
    expect(controller.select).toHaveBeenLastCalledWith(2);
    expect(screen.getByText(/single-year quantile cross-section/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '61.7. assumption provenance. Activate to pin.' }));
    expect(readProvenance()).toEqual(changed[0].p99);
    expect(onUnavailable).not.toHaveBeenCalled();
    unmount();
    expect(controller.dispose).toHaveBeenCalledTimes(1);
  });

  it('provides camera buttons and keyboard navigation without capturing keys on provenance controls', () => {
    render(<ExposureSurface rows={rows} maximumHours={300} onUnavailable={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Rotate surface left' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rotate surface right' }));
    expect(vi.mocked(controller.rotate).mock.calls).toEqual([[-0.15], [0.15]]);
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    expect(vi.mocked(controller.zoom).mock.calls).toEqual([[1.1], [1 / 1.1]]);
    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }));

    const stage = screen.getByRole('group', { name: 'Interactive modeled exposure quantile surface' });
    vi.mocked(controller.rotate).mockClear();
    vi.mocked(controller.zoom).mockClear();
    for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '-', 'Home']) fireEvent.keyDown(stage, { key });
    expect(vi.mocked(controller.rotate).mock.calls).toEqual([[-0.12], [0.12], [0, -0.1], [0, 0.1]]);
    expect(vi.mocked(controller.zoom).mock.calls).toEqual([[1.1], [1 / 1.1]]);
    expect(controller.reset).toHaveBeenCalledTimes(2);

    act(() => callbacks.labels([{ id: 'year-1', kind: 'year', datum: rows[0].year, x: 20, y: 30, anchorX: 20, anchorY: 30 }]));
    vi.mocked(controller.rotate).mockClear();
    fireEvent.keyDown(within(stage).getByRole('button'), { key: 'ArrowLeft' });
    expect(controller.rotate).not.toHaveBeenCalled();
  });

  it('requests the fallback when WebGL is unsupported or renderer initialization fails', () => {
    const onUnavailable = vi.fn();
    vi.stubGlobal('WebGL2RenderingContext', undefined);
    const first = render(<ExposureSurface rows={rows} maximumHours={300} onUnavailable={onUnavailable} />);
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    expect(rendererMock).not.toHaveBeenCalled();
    first.unmount();

    vi.stubGlobal('WebGL2RenderingContext', class {});
    rendererMock.mockImplementationOnce(() => { throw new Error('GPU initialization unavailable'); });
    render(<ExposureSurface rows={rows} maximumHours={300} onUnavailable={onUnavailable} />);
    expect(onUnavailable).toHaveBeenCalledTimes(2);
  });

  it('routes data-update and context-loss failures to the current fallback handler', () => {
    const firstUnavailable = vi.fn();
    const latestUnavailable = vi.fn();
    const { rerender } = render(<ExposureSurface rows={rows} maximumHours={300} onUnavailable={firstUnavailable} />);
    vi.mocked(controller.update).mockImplementationOnce(() => { throw new Error('Unable to update surface'); });
    rerender(<ExposureSurface rows={[row(1, 0)]} maximumHours={300} onUnavailable={latestUnavailable} />);
    expect(firstUnavailable).not.toHaveBeenCalled();
    expect(latestUnavailable).toHaveBeenCalledTimes(1);
    act(() => callbacks.unavailable());
    expect(latestUnavailable).toHaveBeenCalledTimes(2);
  });
});
