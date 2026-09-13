// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAnimatedNumber } from './useAnimatedNumber';

let frames: Map<number, FrameRequestCallback>;
let now: number;
let hidden: boolean;
let reduced: boolean;
let preferenceListeners: Set<() => void>;
beforeEach(() => {
  frames = new Map();
  now = 0;
  hidden = false;
  reduced = false;
  preferenceListeners = new Set();
  let next = 0;
  vi.stubGlobal('matchMedia', () => ({
    get matches() { return reduced; },
    addEventListener: (_: string, listener: () => void) => preferenceListeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => preferenceListeners.delete(listener),
  }));
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { frames.set(++next, callback); return next; });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => { frames.delete(id); });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function advance(milliseconds: number) {
  now += milliseconds;
  act(() => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(now)); });
}

it('eases monotonically to the exact target and leaves no idle frames', () => {
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 10 } });
  expect(frames.size).toBe(0);
  rerender({ value: 100 });
  expect(frames.size).toBe(1);
  advance(160);
  expect(result.current).toBeGreaterThan(55); // Eased progress is ahead of a linear midpoint.
  expect(result.current).toBeLessThan(100);
  const halfway = result.current;
  advance(80);
  expect(result.current).toBeGreaterThan(halfway);
  expect(result.current).toBeLessThan(100);
  advance(80);
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
  rerender({ value: 100 });
  expect(frames.size).toBe(0);
});

it('keeps moving during rapid slider retargets instead of waiting for dragging to stop', () => {
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 0 } });
  for (let target = 100; target <= 800; target += 100) {
    const previous = result.current;
    rerender({ value: target });
    expect(result.current).toBe(previous);
    expect(frames.size).toBe(1);
    advance(16);
    expect(result.current).toBeGreaterThan(previous);
    expect(result.current).toBeLessThan(target);
  }
  advance(320);
  expect(result.current).toBe(800);
  expect(frames.size).toBe(0);
});

it('reverses from the currently drawn value without overshoot and cancels on unmount', () => {
  const { result, rerender, unmount } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 0 } });
  rerender({ value: 100 }); advance(100);
  const displayed = result.current;
  const superseded = [...frames.values()][0];
  rerender({ value: 20 });
  expect(frames.size).toBe(1);
  expect(result.current).toBe(displayed);
  act(() => superseded(now + 16));
  expect(result.current).toBe(displayed);
  expect(frames.size).toBe(1);
  advance(16);
  expect(result.current).toBeLessThan(displayed);
  expect(result.current).toBeGreaterThan(20);
  unmount();
  expect(frames.size).toBe(0);
  expect(preferenceListeners.size).toBe(0);
});

it('shows exact values without scheduling animation for reduced motion', () => {
  reduced = true;
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 0 } });
  rerender({ value: 100 });
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
});

it('settles an in-flight value when reduced motion is enabled and resumes for later changes', () => {
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 0 } });
  rerender({ value: 100 }); advance(16);
  act(() => { reduced = true; preferenceListeners.forEach(listener => listener()); });
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
  act(() => { reduced = false; preferenceListeners.forEach(listener => listener()); });
  expect(frames.size).toBe(0);
  rerender({ value: 200 }); advance(16);
  expect(result.current).toBeGreaterThan(100);
  expect(result.current).toBeLessThan(200);
});

it('finishes active work when the tab becomes hidden and stays idle for hidden recomputes', () => {
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 0 } });
  rerender({ value: 100 }); advance(16);
  act(() => { hidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
  rerender({ value: 200 });
  expect(result.current).toBe(200);
  expect(frames.size).toBe(0);
  act(() => { hidden = false; document.dispatchEvent(new Event('visibilitychange')); });
  expect(frames.size).toBe(0);
  rerender({ value: 300 }); advance(16);
  expect(result.current).toBeGreaterThan(200);
  expect(result.current).toBeLessThan(300);
});

it('settles when animation is disabled and starts later changes from that exact value', () => {
  const { result, rerender } = renderHook(({ value, enabled }) => useAnimatedNumber(value, enabled), {
    initialProps: { value: 0, enabled: true },
  });
  rerender({ value: 100, enabled: true }); advance(16);
  rerender({ value: 100, enabled: false });
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
  rerender({ value: 200, enabled: true }); advance(16);
  expect(result.current).toBeGreaterThan(100);
  expect(result.current).toBeLessThan(200);
});

it.each([0, -1, Number.POSITIVE_INFINITY, Number.NaN])('avoids a loop for invalid duration %s', duration => {
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value, true, duration), { initialProps: { value: 0 } });
  rerender({ value: 100 });
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
});

it('does not let a nonfinite presentation value poison subsequent valid values', () => {
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 0 } });
  rerender({ value: Number.NaN });
  expect(result.current).toBeNaN();
  expect(frames.size).toBe(0);
  rerender({ value: 100 });
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
  rerender({ value: 200 }); advance(320);
  expect(result.current).toBe(200);
  expect(frames.size).toBe(0);
});
