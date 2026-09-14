// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ScenarioErrorBoundary } from './ScenarioErrorBoundary'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('replaces failed results with a visible error and can reset the scenario tree', () => {
  const log = vi.spyOn(console, 'error').mockImplementation(() => {})
  let fails = true
  function Scenario() {
    if (fails) throw new RangeError('Test invariant failed')
    return <p>Fresh scenario</p>
  }
  render(<ScenarioErrorBoundary><Scenario /></ScenarioErrorBoundary>)
  expect(screen.getByRole('alert').textContent).toContain('Test invariant failed')
  expect(screen.queryByText('Fresh scenario')).toBeNull()
  expect(log).toHaveBeenCalled()
  fails = false
  fireEvent.click(screen.getByRole('button', { name: 'Reset analysis' }))
  expect(screen.getByText('Fresh scenario')).toBeTruthy()
  expect(screen.queryByRole('alert')).toBeNull()
})
