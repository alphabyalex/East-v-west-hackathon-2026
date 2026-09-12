// @vitest-environment jsdom
import { beforeAll, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, cleanup, within } from '@testing-library/react';
import { cloneElement, type ReactElement } from 'react';
import App from './App';

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
afterEach(cleanup);

describe('scenario workspace interactions', () => {
  it('moves through every decision state and resets the scenario', () => {
    render(<App />);
    const slider = screen.getByRole('slider', { name: 'Site exposure factor' });
    expect(screen.getByRole('heading', { name: 'worth it' })).toBeTruthy();
    fireEvent.change(slider, { target: { value: '0.6' } });
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
    expect(screen.getByText('No decision crossover within this slider range')).toBeTruthy();
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
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(11);
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
