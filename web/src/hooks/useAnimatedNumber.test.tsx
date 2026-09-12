// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAnimatedNumber } from './useAnimatedNumber';

let frames: Map<number, FrameRequestCallback>;
beforeEach(() => {
  frames = new Map();
  let next = 0;
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { frames.set(++next, callback); return next; });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => { frames.delete(id); });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function tick(time: number) {
  act(() => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(time)); });
}

it('leaves no idle frames after an input animation finishes', () => {
  const { result, rerender } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 10 } });
  expect(frames.size).toBe(0);
  rerender({ value: 100 });
  expect(frames.size).toBe(1);
  tick(0); tick(125);
  expect(result.current).toBeGreaterThan(10);
  expect(result.current).toBeLessThan(100);
  tick(250);
  expect(result.current).toBe(100);
  expect(frames.size).toBe(0);
});

it('cancels superseded frames and all pending work on unmount', () => {
  const { result, rerender, unmount } = renderHook(({ value }) => useAnimatedNumber(value), { initialProps: { value: 0 } });
  rerender({ value: 100 }); tick(0); tick(100);
  const displayed = result.current;
  rerender({ value: 20 });
  expect(frames.size).toBe(1);
  tick(200);
  expect(result.current).toBe(displayed);
  unmount();
  expect(frames.size).toBe(0);
});
