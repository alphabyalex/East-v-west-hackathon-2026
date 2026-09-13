// @vitest-environment jsdom
import { cloneElement, type ReactElement } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { SensitivityPanel } from './SensitivityPanel'
import { buildSensitivity } from '../model/sensitivity'
import { createMockEstimate, defaultInputs, toEstimateRequest, type ScenarioInputs } from '../model'
import { validateEconomicsAssumptions } from '../api/assumptions'
import assumptionsJson from '../model/economics-assumptions.json'

// Keep Recharts and its actual SVG; supply only the layout jsdom does not have.
vi.mock('recharts', async importOriginal => {
  const real = await importOriginal<typeof import('recharts')>()
  return { ...real, ResponsiveContainer: ({ children }: { children: ReactElement<{ width: number; height: number }> }) => cloneElement(children, { width: 840, height: 235 }) }
})

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({ matches: true, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  })
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
})
afterEach(cleanup)

function scenario(overrides: Partial<ScenarioInputs> = {}) {
  const inputs = { ...defaultInputs, ...overrides }
  const request = toEstimateRequest(inputs)
  const assumptions = validateEconomicsAssumptions(assumptionsJson)
  return buildSensitivity(createMockEstimate(request, inputs), createMockEstimate({ ...request, site_exposure: 1 }, inputs), inputs, assumptions)
}

function provenance() {
  return JSON.parse(screen.getByRole('tooltip').querySelector('pre')!.textContent!)
}

function showEndpoints() {
  fireEvent.click(screen.getByText('Inspect ranges & decision endpoints'))
  return screen.getByRole('table', { name: 'Endpoint comparisons (rounded) · USD; break-even modeled exposure in hours/year. Exact values in provenance.' })
}

describe('break-even sensitivity panel', () => {
  it('renders real horizontal range bars in impact order and distinguishes the reference from a decision boundary', () => {
    const sensitivity = scenario()
    render(<SensitivityPanel sensitivity={sensitivity} />)
    expect(screen.getByRole('region', { name: 'Break-even sensitivity' })).toBeTruthy()
    const chart = screen.getByRole('group', { name: /Break-even sensitivity tornado chart/ })
    const expectedKeys = sensitivity.rows.filter(row => row.status === 'modeled').map(row => row.key)
    expect([...chart.querySelectorAll('[data-sensitivity-bar]')].map(bar => bar.getAttribute('data-sensitivity-bar'))).toEqual(expectedKeys)
    expect([...document.querySelectorAll('[data-sensitivity-range]')].map(row => row.getAttribute('data-sensitivity-range'))).toEqual(expectedKeys)
    expect(chart.querySelectorAll('rect').length).toBeGreaterThanOrEqual(3)
    expect(screen.getByText(/The reference line is not the decision threshold/)).toBeTruthy()
    expect(screen.getByText(/Earlier-access contribution stays fixed/)).toBeTruthy()
    for (const row of sensitivity.rows) {
      if (row.status !== 'modeled') continue
      const range = document.querySelector(`[data-sensitivity-range="${row.key}"]`)! as HTMLElement
      expect(within(range).getByRole('button', { name: new RegExp(`^${row.label} low decision: ${row.low.snapshot.decision.replaceAll('_', ' ')}`) })).toBeTruthy()
      expect(within(range).getByRole('button', { name: new RegExp(`^${row.label} high decision: ${row.high.snapshot.decision.replaceAll('_', ' ')}`) })).toBeTruthy()
    }
    expect(screen.getByText('Assumed sensitivity')).toBeTruthy()
    expect(screen.getByText(/not quantiles of total contract loss/)).toBeTruthy()
  })

  it('never draws or assigns an impact number to electricity and utilization', () => {
    const sensitivity = scenario()
    render(<SensitivityPanel sensitivity={sensitivity} />)
    const blocks = document.querySelectorAll('[data-sensitivity-unmodeled]')
    expect(blocks).toHaveLength(2)
    expect(screen.getAllByText('Not modeled')).toHaveLength(2)
    for (const key of ['electricity_price', 'utilization']) {
      expect(document.querySelector(`[data-sensitivity-bar="${key}"]`)).toBeNull()
      expect(document.querySelector(`[data-sensitivity-range="${key}"]`)).toBeNull()
    }
    expect(screen.getByText(/does not mean they have zero economic effect/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Electricity price documented low/ }))
    const row = sensitivity.rows.find(item => item.key === 'electricity_price')!
    expect(provenance()).toEqual(row.low.input)
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: /Utilization documented high/ }))
    expect(provenance()).toEqual(sensitivity.rows.find(item => item.key === 'utilization')!.high.input)
  })

  it('makes every endpoint input, comparison, delta, decision and break-even source accessible on click', () => {
    const sensitivity = scenario()
    render(<SensitivityPanel sensitivity={sensitivity} />)
    const table = showEndpoints()
    expect(within(table).getAllByRole('row')).toHaveLength(7)
    for (const row of sensitivity.rows) {
      if (row.status !== 'modeled') continue
      for (const bound of ['low', 'high'] as const) {
        const endpoint = row[bound]
        const fields = [
          ['input', endpoint.input],
          ['change', endpoint.delta_value_usd],
          ['contract comparison', endpoint.snapshot.net_value_usd.p50],
        ] as const
        for (const [name, datum] of fields) {
          fireEvent.click(within(table).getByRole('button', { name: new RegExp(`^${row.label} ${bound} ${name}:`) }))
          expect(provenance()).toEqual(datum)
          fireEvent.keyDown(document, { key: 'Escape' })
        }
        fireEvent.click(within(table).getByRole('button', { name: new RegExp(`^${row.label} ${bound} decision:`) }))
        expect(provenance()).toEqual({ value: endpoint.snapshot.decision, ...endpoint.snapshot.source })
        fireEvent.keyDown(document, { key: 'Escape' })
        fireEvent.click(within(table).getByRole('button', { name: new RegExp(`^${row.label} ${bound} break-even`) }))
        expect(provenance().ref).toBe(endpoint.snapshot.breakeven_hours.ref)
        if (endpoint.snapshot.breakeven_hours.value !== null) expect(provenance()).toEqual(endpoint.snapshot.breakeven_hours)
        fireEvent.keyDown(document, { key: 'Escape' })
      }
    }
  })

  it('exposes chart-scale provenance by keyboard and retains the raw USD values behind rounded amounts', () => {
    const sensitivity = scenario()
    render(<SensitivityPanel sensitivity={sensitivity} />)
    const chart = screen.getByRole('group', { name: /Break-even sensitivity tornado chart/ })
    const ticks = within(chart).getAllByRole('button')
    expect(ticks).toHaveLength(5)
    for (const tick of ticks) {
      fireEvent.keyDown(tick, { key: 'Enter' })
      expect(provenance().source_type).toBe('assumption')
      expect(provenance().ref).toContain('tick * 1000000 = delta USD')
      fireEvent.keyDown(document, { key: 'Escape' })
    }
    fireEvent.click(screen.getByRole('button', { name: /^Current sensitivity contract comparison:/ }))
    expect(provenance()).toEqual(sensitivity.baseline.net_value_usd.p50)
    expect(screen.getByText(/Displayed amounts are rounded/)).toBeTruthy()
  })

  it('recomputes range geometry, decisions and pinned source values with the scenario, including zero exposure', () => {
    const initial = scenario()
    const { rerender } = render(<SensitivityPanel sensitivity={initial} />)
    fireEvent.click(screen.getByRole('button', { name: /^Current sensitivity contract comparison:/ }))
    const previous = provenance().value
    const changed = scenario({ site_exposure: 0.9 })
    rerender(<SensitivityPanel sensitivity={changed} />)
    expect(provenance().value).not.toBe(previous)
    expect(provenance()).toEqual(changed.baseline.net_value_usd.p50)
    expect(screen.getByRole('button', { name: /^Current sensitivity decision: not worth it/ })).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    rerender(<SensitivityPanel sensitivity={scenario({ site_exposure: 0 })} />)
    expect(document.querySelector('[data-sensitivity-bar="site_exposure"] rect')).toBeTruthy()
    expect(document.querySelector('[data-sensitivity-bar="gpu_rental_price"] line')).toBeTruthy()
    expect(document.querySelector('[data-sensitivity-bar="flexibility_split"] line')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/NaN|Infinity/)
  })
})
