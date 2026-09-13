// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, act } from '@testing-library/react'
import { realGridFixture } from '../api/grid-impact-fixtures'
import { powerScenario } from '../api/grid-impact'
import GridImpactSurface from './GridImpactSurface'
import { createSurfaceRenderer } from './exposure-surface/renderer'
vi.mock('./exposure-surface/renderer', () => ({ createSurfaceRenderer: vi.fn() }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('uses shared rotation/zoom and nearest supplied-bin inspection without inventing heights', () => {
  vi.stubGlobal('WebGL2RenderingContext', class {})
  vi.stubGlobal('matchMedia', (media: string) => ({ matches: true, media, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  const data = realGridFixture('OKGE')
  if (data.cheap_power.status !== 'observed_hours_only') throw new Error('missing evidence')
  const bins = powerScenario(data.cheap_power, 100, .5).bins
  const controller = { update: vi.fn(), updateGrid: vi.fn(), select: vi.fn(), rotate: vi.fn(), reset: vi.fn(), zoom: vi.fn(), dispose: vi.fn() }
  let callbacks!: Parameters<typeof createSurfaceRenderer>[1]
  vi.mocked(createSurfaceRenderer).mockImplementation((_host, value) => { callbacks = value; return controller })
  const page = render(<GridImpactSurface bins={bins} onUnavailable={vi.fn()} />)
  const grid = controller.updateGrid.mock.calls[0][0]
  expect(grid.rows).toHaveLength(12)
  expect(grid.columns).toHaveLength(24)
  expect(grid.values.flat()).toEqual(bins.map(bin => bin.dollars))
  fireEvent.click(screen.getByRole('button', { name: 'Rotate surface right' }))
  expect(controller.rotate).toHaveBeenCalledWith(.15)
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
  expect(controller.zoom).toHaveBeenCalledWith(1.1)
  act(() => callbacks.select(82))
  expect((screen.getByRole('combobox', { name: 'Inspect grid-impact month' }) as HTMLSelectElement).value).toBe('3')
  expect((screen.getByRole('combobox', { name: 'Inspect grid-impact UTC hour' }) as HTMLSelectElement).value).toBe('10')
  fireEvent.change(screen.getByRole('combobox', { name: 'Inspect grid-impact UTC hour' }), { target: { value: '11' } })
  expect(controller.select).toHaveBeenLastCalledWith(83)
  page.unmount(); expect(controller.dispose).toHaveBeenCalledTimes(1)
})
