// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Overview } from './Overview'
import manifest from '../../../data/processed/national_stack/zone_rankings.json'

beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Overview must never request data') }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('demonstrates the slider entirely locally with sourced preview values and no requests', () => {
  const errors = vi.spyOn(console, 'error')
  const onScenario = vi.fn(), onZones = vi.fn()
  const view = render(<Overview onScenario={onScenario} onZones={onZones} />)
  const slider = screen.getByRole('slider', { name: 'Preview site exposure factor' })
  expect(screen.queryByText(/Illustrative example, not a model estimate/)).toBeNull()
  expect(screen.getByText('INTERACTIVE PREVIEW')).toBeTruthy()
  expect(document.body.textContent).not.toMatch(/sample|illustrative/i)
  for (const [factor, hours] of [[0, '0.0'], [.6, '60.0'], [.85, '85.0'], [1, '100.0']] as const) {
    expect(() => fireEvent.change(slider, { target: { value: String(factor) } })).not.toThrow()
    const value = screen.getByRole('button', { name: new RegExp(`^Exposure: ${hours}`) })
    expect(JSON.parse(value.getAttribute('data-provenance')!)).toMatchObject({ value: factor * 100, source_type: 'assumption' })
    fireEvent.click(value)
    expect(JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!).ref).toContain('mock://overview/interaction')
    fireEvent.keyDown(document, { key: 'Escape' })
  }
  expect(fetch).not.toHaveBeenCalled()
  expect(onScenario).not.toHaveBeenCalled()
  expect(onZones).not.toHaveBeenCalled()
  expect(errors).not.toHaveBeenCalled()
  errors.mockRestore()
  view.unmount()
  render(<Overview onScenario={onScenario} onZones={onZones} />)
  expect((screen.getByRole('slider') as HTMLInputElement).value).toBe('0.4')
})

it('uses the shipped evidence count, keeps research limitations, and connects both destinations', () => {
  const onScenario = vi.fn(), onZones = vi.fn()
  render(<Overview onScenario={onScenario} onZones={onZones} />)
  const count = screen.getByRole('button', { name: /^Zones with supplied wind-screening evidence:/ })
  expect(JSON.parse(count.getAttribute('data-provenance')!)).toEqual({
    value: new Set(manifest.available_wind_evidence.map(row => row.location_id)).size,
    source_type: 'data', ref: expect.stringContaining('zone_rankings.json#/available_wind_evidence'),
  })
  expect(screen.getByText(/synthetic regional proxies are research work, not validated national coverage/)).toBeTruthy()
  expect(screen.getByText(/We never fabricate a score/)).toBeTruthy()
  expect(screen.queryByText(/Save candidate sites with their scenario inputs/)).toBeNull()
  expect(screen.queryByText(/Thresholds are static checks/)).toBeNull()
  expect(screen.queryByRole('button', { name: 'Build your first scenario' })).toBeNull()
  expect(screen.getByText(/not a permanent ceiling/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Open scenario stress test' }))
  expect(onScenario).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Explore SPP zone evidence' }))
  fireEvent.click(screen.getByRole('button', { name: 'Inspect zone evidence' }))
  expect(onZones).toHaveBeenCalledTimes(2)
  expect(fetch).not.toHaveBeenCalled()
})
