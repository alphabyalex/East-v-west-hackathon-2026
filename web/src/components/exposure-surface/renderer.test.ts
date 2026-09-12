// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi, type Mock, type MockInstance } from 'vitest'
import { BufferGeometry, Line, Mesh, OrthographicCamera, Points, Raycaster, Scene, type Material } from 'three'
import type { BaselineYear, SourcedValue } from '../../model'
import { createSurfaceRenderer, type SurfaceController } from './renderer'

interface RendererDouble {
  domElement: HTMLCanvasElement
  render: Mock<(scene: Scene, camera: OrthographicCamera) => void>
  setSize: Mock
  dispose: Mock
  forceContextLoss: Mock
}

const doubles = vi.hoisted(() => ({ renderers: [] as RendererDouble[] }))

// Keep actual Three.js geometry, materials, projection, and OrbitControls. Only
// replace the GPU boundary; jsdom cannot create a WebGL rendering context.
vi.mock('three', async importOriginal => {
  const real = await importOriginal<typeof import('three')>()
  class Renderer {
    domElement = document.createElement('canvas')
    setClearColor = vi.fn()
    setPixelRatio = vi.fn()
    setSize = vi.fn()
    dispose = vi.fn()
    forceContextLoss = vi.fn()
    render = vi.fn((scene: Scene, camera: OrthographicCamera) => {
      // These matrix updates normally happen inside WebGLRenderer.render.
      scene.updateMatrixWorld()
      camera.updateMatrixWorld()
    })
    constructor() { doubles.renderers.push(this) }
  }
  return { ...real, WebGLRenderer: Renderer }
})

let reducedMotion = true
let pendingFrames: Map<number, FrameRequestCallback>
let observe: Mock
let disconnect: Mock
let resize: ResizeObserverCallback
let active: SurfaceController[]

beforeEach(() => {
  doubles.renderers.length = 0
  active = []
  reducedMotion = true
  pendingFrames = new Map()
  let nextFrame = 0
  vi.stubGlobal('matchMedia', vi.fn((media: string) => ({
    get matches() { return reducedMotion }, media,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })))
  observe = vi.fn()
  disconnect = vi.fn()
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resize = callback }
    observe = observe
    disconnect = disconnect
  })
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => {
    pendingFrames.set(++nextFrame, callback)
    return nextFrame
  })
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => { pendingFrames.delete(id) })
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
})

afterEach(() => {
  active.forEach(controller => controller.dispose())
  document.body.replaceChildren()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function mount() {
  const host = document.createElement('div')
  Object.defineProperties(host, {
    clientWidth: { configurable: true, value: 640 },
    clientHeight: { configurable: true, value: 360 },
  })
  document.body.append(host)
  const callbacks = { labels: vi.fn(), select: vi.fn(), unavailable: vi.fn() }
  const controller = createSurfaceRenderer(host, callbacks)
  active.push(controller)
  const renderer = doubles.renderers.at(-1)!
  flushFrame(0)
  const [scene, camera] = renderer.render.mock.lastCall!
  const surface = () => {
    let result: Mesh<BufferGeometry> | undefined
    scene.traverse(object => { if (object instanceof Mesh) result = object })
    return result!
  }
  return { host, callbacks, controller, renderer, scene, camera, surface }
}

const sourced = (value: number, ref: string): SourcedValue => ({ value, source_type: 'assumption', ref })
const rows = (siteExposure = 1, contractYears = 2): BaselineYear[] => Array.from({ length: contractYears }, (_, index) => ({
  year: sourced(index + 1, `mock://renderer/year/${index + 1}`),
  p50: sourced((100 + index * 10) * siteExposure, `mock://renderer/year/${index + 1}/p50`),
  p90: sourced((300 + index * 15) * siteExposure, `mock://renderer/year/${index + 1}/p90`),
  p99: sourced((500 + index * 20) * siteExposure, `mock://renderer/year/${index + 1}/p99`),
}))

function flushFrame(time: number) {
  const frames = [...pendingFrames.values()]
  pendingFrames.clear()
  frames.forEach(callback => callback(time))
}

describe('exposure surface renderer', () => {
  it('draws supplied observations on a fixed scale and anchors selection to the same vertex', () => {
    const { controller, scene, surface } = mount()
    const fullExposure = rows()
    controller.update(fullExposure, 1000)
    flushFrame(0)
    const geometry = surface().geometry
    expect(geometry.index!.count).toBe(12)
    const positions = geometry.getAttribute('position')
    fullExposure.flatMap(row => [row.p50, row.p90, row.p99]).forEach((datum, index) => {
      expect(positions.getY(index)).toBeCloseTo(datum.value / 1000 * 5, 5)
    })
    controller.select(4)
    flushFrame(0)
    let selection: Points<BufferGeometry> | undefined
    let guide: Line<BufferGeometry> | undefined
    scene.traverse(object => {
      if (object instanceof Points && object.renderOrder === 5) selection = object
      if (object instanceof Line && object.renderOrder === 4) guide = object
    })
    const vertex = [positions.getX(4), positions.getY(4), positions.getZ(4)]
    expect(Array.from(selection!.geometry.getAttribute('position').array)).toEqual(vertex)
    const guideVertices = Array.from(guide!.geometry.getAttribute('position').array)
    expect(guideVertices.slice(3)).toEqual(vertex)
    expect(guideVertices[0]).toBe(vertex[0])
    expect(guideVertices[1]).toBeCloseTo(0.01)
    expect(guideVertices[2]).toBe(vertex[2])

    controller.update(rows(0), 1000)
    flushFrame(0)
    const zero = surface().geometry.getAttribute('position')
    for (let index = 0; index < zero.count; index++) expect(zero.getY(index)).toBe(0)
    expect(pendingFrames.size).toBe(0)
  })

  it('renders camera interaction on demand and restores the initial camera without an idle animation loop', () => {
    const { controller, renderer, camera } = mount()
    controller.update(rows(), 1000)
    flushFrame(0)
    const initialPosition = camera.position.clone()
    const initialDraws = renderer.render.mock.calls.length
    controller.rotate(0.2, 0.1)
    expect(camera.position.distanceTo(initialPosition)).toBeGreaterThan(0.1)
    expect(renderer.render).toHaveBeenCalledTimes(initialDraws)
    controller.zoom(1.2)
    expect(camera.zoom).toBeCloseTo(1.2)
    controller.reset()
    expect(camera.position.distanceTo(initialPosition)).toBeLessThan(1e-10)
    expect(camera.zoom).toBe(1)
    expect(pendingFrames.size).toBe(1)
    flushFrame(0)
    expect(renderer.render).toHaveBeenCalledTimes(initialDraws + 1)
    expect(pendingFrames.size).toBe(0)
  })

  it('projects finite sourced axis labels and updates their positions when the viewport changes', () => {
    const { controller, callbacks, host, renderer } = mount()
    const annualRows = rows(1, 7)
    controller.update(annualRows, 1000)
    flushFrame(0)
    const labels = callbacks.labels.mock.lastCall![0]
    expect(new Set(labels.map((label: { kind: string }) => label.kind)))
      .toEqual(new Set(['year', 'percentile', 'hours']))
    for (const label of labels) {
      expect(Number.isFinite(label.datum.value)).toBe(true)
      expect(label.datum.ref).toEqual(expect.any(String))
      expect(label.datum.ref.length).toBeGreaterThan(0)
      expect(['assumption', 'data', 'clause', 'model']).toContain(label.datum.source_type)
      for (const key of ['x', 'y', 'anchorX', 'anchorY']) expect(Number.isFinite(label[key])).toBe(true)
      expect(label.x).toBeGreaterThanOrEqual(0)
      expect(label.x).toBeLessThanOrEqual(640)
      expect(label.y).toBeGreaterThanOrEqual(0)
      expect(label.y).toBeLessThanOrEqual(360)
      if (label.kind === 'year') expect(annualRows.some(row => row.year === label.datum)).toBe(true)
      if (label.kind === 'percentile') expect(label.datum.ref).toContain('not probability density')
    }
    const before = labels.map((label: { x: number; y: number }) => [label.x, label.y])
    Object.defineProperty(host, 'clientWidth', { value: 900 })
    resize([], {} as ResizeObserver)
    flushFrame(0)
    expect(renderer.setSize).toHaveBeenLastCalledWith(900, 360, false)
    expect(callbacks.labels.mock.lastCall![0].map((label: { x: number; y: number }) => [label.x, label.y]))
      .not.toEqual(before)
    expect(observe).toHaveBeenCalledWith(host)
  })

  it('renders a one-year contract as a selectable slice without any fabricated surface faces', () => {
    const { controller, surface, scene } = mount()
    controller.update(rows(1, 1), 1000)
    flushFrame(0)
    expect(surface().geometry.index!.count).toBe(0)
    const positions = surface().geometry.getAttribute('position')
    expect(positions.count).toBe(3)
    for (let index = 0; index < positions.count; index++) expect(positions.getX(index)).toBe(0)
    controller.select(2)
    flushFrame(0)
    let marker: Points<BufferGeometry> | undefined
    scene.traverse(object => { if (object instanceof Points && object.renderOrder === 5) marker = object })
    expect(marker!.geometry.getAttribute('position').getY(0)).toBe(positions.getY(2))
  })

  it('animates only data updates, reaches the exact target, and cancels pending work when disposed', () => {
    reducedMotion = false
    vi.spyOn(performance, 'now').mockReturnValue(1000)
    const { controller, renderer, surface } = mount()
    controller.update(rows(0), 1000)
    flushFrame(1000)
    expect(pendingFrames.size).toBe(0)
    controller.update(rows(1), 1000)
    expect(pendingFrames.size).toBe(1)
    flushFrame(1000)
    expect(surface().geometry.getAttribute('position').getY(2)).toBe(0)
    flushFrame(1140)
    const intermediate = surface().geometry.getAttribute('position').getY(2)
    const target = rows(1)[0].p99.value / 1000 * 5
    expect(intermediate).toBeGreaterThan(0)
    expect(intermediate).toBeLessThan(target)
    flushFrame(1280)
    expect(surface().geometry.getAttribute('position').getY(2)).toBeCloseTo(target, 5)
    expect(pendingFrames.size).toBe(0)
    controller.update(rows(0), 1000)
    expect(pendingFrames.size).toBe(1)
    const frameId = [...pendingFrames.keys()][0]
    controller.dispose()
    active = []
    const finalDraws = renderer.render.mock.calls.length
    expect(window.cancelAnimationFrame).toHaveBeenCalledWith(frameId)
    expect(pendingFrames.size).toBe(0)
    flushFrame(1500)
    expect(renderer.render).toHaveBeenCalledTimes(finalDraws)
  })

  it('releases scene resources, DOM listeners, and the observer on disposal', () => {
    const { controller, renderer, scene, callbacks, host } = mount()
    controller.update(rows(), 1000)
    flushFrame(0)
    const resourceDisposals: MockInstance[] = []
    scene.traverse(object => {
      if (object instanceof Mesh || object instanceof Points || object instanceof Line) {
        resourceDisposals.push(vi.spyOn(object.geometry, 'dispose'))
        const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material]
        materials.forEach(material => resourceDisposals.push(vi.spyOn(material, 'dispose')))
      }
    })
    const canvas = renderer.domElement
    const removeListener = vi.spyOn(canvas, 'removeEventListener')
    const lost = new Event('webglcontextlost', { cancelable: true })
    canvas.dispatchEvent(lost)
    expect(lost.defaultPrevented).toBe(true)
    expect(callbacks.unavailable).toHaveBeenCalledOnce()
    controller.dispose()
    active = []
    expect(resourceDisposals.length).toBeGreaterThan(0)
    resourceDisposals.forEach(dispose => expect(dispose).toHaveBeenCalledOnce())
    expect(disconnect).toHaveBeenCalledOnce()
    expect(renderer.dispose).toHaveBeenCalledOnce()
    expect(renderer.forceContextLoss).toHaveBeenCalledOnce()
    expect(host.contains(canvas)).toBe(false)
    for (const event of ['pointerdown', 'pointermove', 'pointerup', 'wheel', 'contextmenu', 'webglcontextlost']) {
      expect(removeListener.mock.calls.some(([type]) => type === event)).toBe(true)
    }
    canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }))
    expect(callbacks.unavailable).toHaveBeenCalledOnce()
  })

  it('coalesces a burst of camera, resize and pointer events into a single draw and raycast pass', () => {
    const { controller, renderer, callbacks } = mount()
    controller.update(rows(), 1000)
    flushFrame(0)
    const canvas = renderer.domElement
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect)
    const raycast = vi.spyOn(Raycaster.prototype, 'intersectObject')
    renderer.render.mockClear()
    callbacks.labels.mockClear()
    for (let index = 0; index < 50; index++) {
      controller.rotate(0.001)
      controller.zoom(1.001)
      resize([], {} as ResizeObserver)
      const event = new Event('pointermove')
      Object.assign(event, { buttons: 0, clientX: 100 + index, clientY: 100 })
      canvas.dispatchEvent(event)
    }
    expect(renderer.render).not.toHaveBeenCalled()
    expect(raycast).not.toHaveBeenCalled()
    expect(pendingFrames.size).toBe(1)
    flushFrame(16)
    expect(renderer.render).toHaveBeenCalledOnce()
    expect(callbacks.labels).toHaveBeenCalledOnce()
    expect(raycast.mock.calls.length).toBeGreaterThan(0)
    expect(raycast.mock.calls.length).toBeLessThanOrEqual(2)
    expect(pendingFrames.size).toBe(0)
    flushFrame(32)
    expect(renderer.render).toHaveBeenCalledOnce()
  })

  it('applies only the latest slider update and reuses GPU geometry/materials without publishing unchanged labels', () => {
    const { controller, surface, callbacks, renderer } = mount()
    controller.update(rows(0), 1000)
    flushFrame(0)
    const original = surface()
    const geometryDispose = vi.spyOn(original.geometry, 'dispose')
    const materialDispose = vi.spyOn(original.material as Material, 'dispose')
    callbacks.labels.mockClear()
    renderer.render.mockClear()
    for (let index = 1; index <= 50; index++) controller.update(rows(index / 50), 1000)
    expect(pendingFrames.size).toBe(1)
    flushFrame(16)
    expect(renderer.render).toHaveBeenCalledOnce()
    expect(surface()).toBe(original)
    expect(surface().geometry.getAttribute('position').getY(2)).toBeCloseTo(2.5)
    expect(geometryDispose).not.toHaveBeenCalled()
    expect(materialDispose).not.toHaveBeenCalled()
    expect(callbacks.labels).not.toHaveBeenCalled()
    expect(pendingFrames.size).toBe(0)
    controller.update(rows(1, 1), 1000)
    flushFrame(32)
    expect(surface()).not.toBe(original)
    expect(geometryDispose).toHaveBeenCalledOnce()
    expect(materialDispose).toHaveBeenCalledOnce()
  })

  it('cancels all work while hidden, then resumes with the latest inputs and becomes idle', () => {
    reducedMotion = false
    const { controller, renderer, surface, callbacks } = mount()
    controller.update(rows(0), 1000)
    flushFrame(0)
    controller.update(rows(1), 1000)
    flushFrame(16)
    expect(pendingFrames.size).toBe(1)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    const draws = renderer.render.mock.calls.length
    const labelUpdates = callbacks.labels.mock.calls.length
    expect(pendingFrames.size).toBe(0)
    controller.rotate(0.1)
    controller.update(rows(0.25), 1000)
    resize([], {} as ResizeObserver)
    flushFrame(1000)
    expect(renderer.render).toHaveBeenCalledTimes(draws)
    expect(callbacks.labels).toHaveBeenCalledTimes(labelUpdates)
    expect(pendingFrames.size).toBe(0)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    document.dispatchEvent(new Event('visibilitychange'))
    flushFrame(1000)
    flushFrame(1280)
    expect(surface().geometry.getAttribute('position').getY(2)).toBeCloseTo(0.625)
    expect(pendingFrames.size).toBe(0)
  })

  it('stops immediately after a draw failure or context loss, even before the React fallback unmounts it', () => {
    const { controller, renderer, callbacks, host } = mount()
    controller.update(rows(), 1000)
    renderer.render.mockImplementationOnce(() => { throw new Error('GPU draw failed') })
    flushFrame(16)
    expect(callbacks.unavailable).toHaveBeenCalledOnce()
    expect(renderer.dispose).toHaveBeenCalledOnce()
    expect(host.contains(renderer.domElement)).toBe(false)
    controller.rotate(0.1)
    controller.reset()
    controller.zoom(1.1)
    controller.select(1)
    controller.update(rows(), 1000)
    document.dispatchEvent(new Event('visibilitychange'))
    controller.dispose()
    expect(pendingFrames.size).toBe(0)
    expect(renderer.dispose).toHaveBeenCalledOnce()
    expect(callbacks.unavailable).toHaveBeenCalledOnce()
  })

  it('releases a partially initialized WebGL context if setup fails before a controller can be returned', () => {
    observe.mockImplementationOnce(() => { throw new Error('Unable to observe surface') })
    const host = document.createElement('div')
    document.body.append(host)
    expect(() => createSurfaceRenderer(host, { labels: vi.fn(), select: vi.fn(), unavailable: vi.fn() }))
      .toThrow('Unable to observe surface')
    const renderer = doubles.renderers.at(-1)!
    expect(renderer.dispose).toHaveBeenCalledOnce()
    expect(renderer.forceContextLoss).toHaveBeenCalledOnce()
    expect(disconnect).toHaveBeenCalledOnce()
    expect(host.childElementCount).toBe(0)
    expect(pendingFrames.size).toBe(0)
  })
})
