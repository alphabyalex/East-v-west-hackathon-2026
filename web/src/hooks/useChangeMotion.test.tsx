// @vitest-environment jsdom
import { useRef } from 'react'
import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChangeMotion } from './useChangeMotion'

const cancel = vi.fn()
const animate = vi.fn(() => ({ cancel }))
let reduced = false
const listeners = new Set<() => void>()

function Readout({ value }: { value: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useChangeMotion(ref, value)
  return <div ref={ref}>{value}</div>
}

beforeEach(() => {
  reduced = false
  listeners.clear()
  vi.stubGlobal('matchMedia', () => ({
    matches: reduced,
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  }))
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks() })

describe('discrete result motion', () => {
  it('animates only changed results and cancels superseded/unmounted work', () => {
    const view = render(<Readout value="worth it" />)
    expect(animate).not.toHaveBeenCalled()
    view.rerender(<Readout value="close call" />)
    expect(animate).toHaveBeenCalledTimes(1)
    expect(animate.mock.calls[0]).toEqual([
      [{ opacity: 0.55 }, { opacity: 1 }],
      { duration: 320, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    ])
    view.rerender(<Readout value="close call" />)
    expect(animate).toHaveBeenCalledTimes(1)
    view.rerender(<Readout value="not worth it" />)
    expect(cancel).toHaveBeenCalledTimes(1)
    view.unmount()
    expect(cancel).toHaveBeenCalledTimes(2)
    expect(listeners.size).toBe(0)
  })

  it('respects reduced motion immediately and cancels when the preference changes', () => {
    const view = render(<Readout value="fan" />)
    reduced = true
    view.rerender(<Readout value="surface" />)
    expect(animate).not.toHaveBeenCalled()
    reduced = false
    view.rerender(<Readout value="fan" />)
    expect(animate).toHaveBeenCalledTimes(1)
    listeners.forEach(listener => listener())
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('does no animation in a hidden tab or without browser animation support', () => {
    const view = render(<Readout value="fan" />)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    view.rerender(<Readout value="surface" />)
    expect(animate).not.toHaveBeenCalled()
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: undefined })
    view.rerender(<Readout value="fan" />)
    expect(animate).not.toHaveBeenCalled()
  })
})
