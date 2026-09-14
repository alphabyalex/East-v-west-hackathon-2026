// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within, act } from '@testing-library/react'
import { cloneElement, type ReactElement } from 'react'
import { GridImpactPanel } from './GridImpactPanel'
import { realGridFixture } from '../api/grid-impact-fixtures'

const state = vi.hoisted(() => ({ inputs: { location_id: 'LES', load_mw: 100 } }))
vi.mock('../ScenarioContext', () => ({ useScenario: () => state }))
vi.mock('recharts', async original => ({ ...await original<typeof import('recharts')>(), ResponsiveContainer: ({ children }: { children: ReactElement<{ width: number; height: number }> }) => cloneElement(children, { width: 900, height: 230 }) }))
beforeEach(() => {
  state.inputs = { location_id: 'LES', load_mw: 100 }
  vi.stubGlobal('matchMedia', (media: string) => ({ matches: true, media, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(realGridFixture(url.split('/').at(-1)!)))))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('renders real LES wind and cheap-power value with honest carbon framing and clickable scenario provenance', async () => {
  render(<GridImpactPanel />)
  const money = await screen.findByRole('button', { name: /Cheap-power value: \$397,027/ })
  expect(screen.getByRole('button', { name: /Wind absorption scenario: 38,200/ })).toBeTruthy()
  expect(screen.getByRole('button', { name: /Carbon shifted: Unavailable/ })).toBeTruthy()
  expect(screen.getByText(/not carbon absorbed, removed, or avoided/)).toBeTruthy()
  fireEvent.click(money)
  const source = JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!)
  expect(source.value).toBeCloseTo(397026.85, 6)
  expect(source.source_type).toBe('assumption')
  expect(source.ref).toContain('selected_load_mw=100')
  expect(source.ref).toContain('upward_available_fraction=0.5')
  expect(source.ref).toContain('not measured savings')
})

it('recomputes with selected load and separate availability without another location request', async () => {
  const page = render(<GridImpactPanel />)
  await screen.findByRole('button', { name: /Cheap-power value: \$397,027/ })
  state.inputs.load_mw = 200
  page.rerender(<GridImpactPanel />)
  expect(screen.getByRole('button', { name: /Cheap-power value: \$794,054/ })).toBeTruthy()
  fireEvent.change(screen.getByRole('slider', { name: 'Upward available capacity' }), { target: { value: '0' } })
  expect(screen.getByRole('button', { name: /Cheap-power value: \$0/ })).toBeTruthy()
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('clears prior values immediately on zone change and retains a visible unavailable panel', async () => {
  const page = render(<GridImpactPanel />)
  await screen.findByRole('button', { name: /Cheap-power value: \$397,027/ })
  state.inputs.location_id = 'EDE'
  page.rerender(<GridImpactPanel />)
  expect(screen.queryByRole('button', { name: /Cheap-power value:/ })).toBeNull()
  expect(await screen.findByText('Sustainability and cost data not yet available for this zone')).toBeTruthy()
  expect(fetch).toHaveBeenLastCalledWith(expect.stringMatching(/\/EDE$/), expect.objectContaining({ cache: 'no-store' }))
})

it('ignores a slow response from a previous selection, even if transport ignores abort', async () => {
  let release!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith('/LES') ? new Promise<Response>(resolve => { release = resolve }) : Promise.resolve(new Response(JSON.stringify(realGridFixture('EDE'))))))
  const page = render(<GridImpactPanel />)
  state.inputs.location_id = 'EDE'; page.rerender(<GridImpactPanel />)
  await screen.findByText('Sustainability and cost data not yet available for this zone')
  await act(async () => release(new Response(JSON.stringify(realGridFixture()))))
  expect(screen.queryByRole('button', { name: /Cheap-power value:/ })).toBeNull()
})

it('falls back to monthly values if WebGL is unavailable and preserves source inspection', async () => {
  vi.stubGlobal('WebGL2RenderingContext', undefined)
  render(<GridImpactPanel />)
  await screen.findByRole('button', { name: /Cheap-power value:/ })
  expect(await screen.findByText(/Surface unavailable on this device/)).toBeTruthy()
  fireEvent.click(screen.getByText('Inspect monthly values and sources'))
  expect(within(screen.getByRole('table', { name: 'Sourced monthly grid-impact values' })).getAllByRole('row')).toHaveLength(13)
})

it('makes HTTP failure visible and permits retry without numeric substitutes', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 503 }))
  render(<GridImpactPanel />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry grid impact' }))
  expect(await screen.findByRole('button', { name: /Cheap-power value: \$397,027/ })).toBeTruthy()
})

it('defaults to the surface and lets the user switch to monthly values', async () => {
  const renderer = await import('./exposure-surface/renderer')
  vi.stubGlobal('WebGL2RenderingContext', class {})
  const controller = { update: vi.fn(), updateGrid: vi.fn(), select: vi.fn(), reset: vi.fn(), rotate: vi.fn(), zoom: vi.fn(), dispose: vi.fn() }
  vi.spyOn(renderer, 'createSurfaceRenderer').mockReturnValue(controller)
  render(<GridImpactPanel />)
  const toggle = within(await screen.findByRole('group', { name: 'Grid-impact chart view' }))
  expect(toggle.getByRole('button', { name: 'Surface' }).getAttribute('aria-pressed')).toBe('true')
  await vi.waitFor(() => expect(controller.updateGrid).toHaveBeenCalled())
  fireEvent.click(toggle.getByRole('button', { name: 'Monthly' }))
  expect(screen.getByRole('img', { name: 'Observed wholesale scenario value by month, in US dollars' })).toBeTruthy()
  expect(controller.dispose).toHaveBeenCalled()
})
