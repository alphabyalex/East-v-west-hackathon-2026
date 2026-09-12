import { describe, expect, it } from 'vitest'
import { defaultInputs, deriveScenario, mockResponse, type BaselineYear } from '../../model'
import { buildSurfaceData, QUANTILES, SURFACE_SIZE } from './geometry'

const makeRows = (contractYears = 3): BaselineYear[] => deriveScenario({
  ...defaultInputs,
  contract_years: contractYears,
}).annual_series

describe('exposure quantile surface geometry', () => {
  it.each(Array.from({ length: 20 }, (_, index) => index + 1))(
    'uses only supplied observations for a %i-year contract',
    (contractYears) => {
      for (const location of mockResponse.locations) {
        const rows = deriveScenario({
          ...defaultInputs,
          location_id: location.id,
          contract_years: contractYears,
        }).annual_series
        const surface = buildSurfaceData(rows, 1000)
        expect(surface.positions).toBeInstanceOf(Float32Array)
        expect(surface.indices).toBeInstanceOf(Uint32Array)
        expect(surface.positions).toHaveLength(contractYears * 4 * 3)
        expect(surface.samples).toHaveLength(contractYears * 4)
        expect(surface.indices).toHaveLength((contractYears - 1) * 3 * 2 * 3)

        surface.samples.forEach((sample, index) => {
          const yearIndex = Math.floor(index / QUANTILES.length)
          const quantile = QUANTILES[index % QUANTILES.length]
          expect(sample.yearIndex).toBe(yearIndex)
          expect(sample.quantile).toBe(quantile)
          expect(sample.year).toBe(rows[yearIndex].year)
          expect(sample.exposure).toBe(rows[yearIndex][`p${quantile}`])
          sample.position.forEach((coordinate, axis) => {
            expect(surface.positions[index * 3 + axis]).toBeCloseTo(coordinate, 5)
          })
        })

        // A correctly wound surface faces up, even if exposures decrease over time.
        for (let index = 0; index < surface.indices.length; index += 3) {
          const [a, b, c] = Array.from(surface.indices.slice(index, index + 3))
            .map((sampleIndex) => surface.samples[sampleIndex].position)
          const normalY = (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])
          expect(normalY).toBeGreaterThan(0)
        }
      }
    },
  )

  it('connects adjacent supplied quantiles and years without adding samples', () => {
    const { indices } = buildSurfaceData(makeRows(2), 1000)
    expect(Array.from(indices)).toEqual([
      0, 4, 1, 4, 5, 1,
      1, 5, 2, 5, 6, 2,
      2, 6, 3, 6, 7, 3,
    ])
  })

  it('positions the percentile axis by actual percentile distances', () => {
    const { samples } = buildSurfaceData(makeRows(1), 1000)
    const depths = samples.map((sample) => sample.position[2])
    expect(depths[0]).toBe(SURFACE_SIZE.depth / 2)
    expect(depths[3]).toBe(-SURFACE_SIZE.depth / 2)
    expect(depths[0] - depths[1]).toBeCloseTo(40 / 89 * SURFACE_SIZE.depth, 12)
    expect(depths[1] - depths[2]).toBeCloseTo(40 / 89 * SURFACE_SIZE.depth, 12)
    expect(depths[2] - depths[3]).toBeCloseTo(9 / 89 * SURFACE_SIZE.depth, 12)
  })

  it('positions years by their real intervals rather than their array indices', () => {
    const rows = makeRows()
    rows[0].year = { ...rows[0].year, value: 2027 }
    rows[1].year = { ...rows[1].year, value: 2028 }
    rows[2].year = { ...rows[2].year, value: 2032 }
    const { samples } = buildSurfaceData(rows, 1000)
    expect(samples.filter((sample) => sample.quantile === 50).map((sample) => sample.position[0]))
      .toEqual([-5, -3, 5])
  })

  it('uses a fixed linear height scale without suppressing values or giving zero artificial height', () => {
    const rows = makeRows()
    const scaled = buildSurfaceData(rows, 1000)
    scaled.samples.forEach((sample) => {
      expect(sample.position[1]).toBe(sample.exposure.value / 1000 * SURFACE_SIZE.height)
    })
    const zeroRows = deriveScenario({ ...defaultInputs, site_exposure: 0 }).annual_series
    expect(buildSurfaceData(zeroRows, 1000).samples.every((sample) => sample.position[1] === 0))
      .toBe(true)
    const shorterAxis = buildSurfaceData(rows, 100)
    expect(shorterAxis.samples[3].position[1]).toBe(rows[0].p99.value / 100 * SURFACE_SIZE.height)
  })

  it('shows a single supplied year as a slice without inventing any time width', () => {
    const surface = buildSurfaceData(makeRows(1), 1000)
    expect(surface.samples).toHaveLength(4)
    expect(surface.samples.every((sample) => sample.position[0] === 0)).toBe(true)
    expect(surface.indices).toHaveLength(0)
  })

  it.each([0, -1, Number.NaN, Infinity, -Infinity])('rejects invalid maximum hours %s', (maximumHours) => {
    expect(() => buildSurfaceData(makeRows(), maximumHours)).toThrow(RangeError)
  })

  it('rejects missing or unsupported contract horizons', () => {
    expect(() => buildSurfaceData([], 1000)).toThrow(RangeError)
    const rows = makeRows(20)
    expect(() => buildSurfaceData([...rows, rows[19]], 1000)).toThrow(RangeError)
  })

  it.each([Number.NaN, Infinity, -Infinity, 1, 0])('rejects invalid or unordered year %s', (year) => {
    const rows = makeRows()
    rows[1].year = { ...rows[1].year, value: year }
    expect(() => buildSurfaceData(rows, 1000)).toThrow(RangeError)
  })

  it.each([Number.NaN, Infinity, -Infinity, -1])('rejects invalid quantile exposure %s', (exposure) => {
    QUANTILES.forEach((quantile) => {
      const rows = makeRows()
      rows[0][`p${quantile}`] = { ...rows[0][`p${quantile}`], value: exposure }
      expect(() => buildSurfaceData(rows, 1000)).toThrow(RangeError)
    })
  })

  it('rejects quantiles that cross and accepts equal quantiles', () => {
    const rows = makeRows()
    rows[0].p90 = { ...rows[0].p90, value: rows[0].p50.value - 1 }
    expect(() => buildSurfaceData(rows, 1000)).toThrow(RangeError)
    rows[0].p90 = { ...rows[0].p90, value: rows[0].p50.value }
    expect(() => buildSurfaceData(rows, 1000)).not.toThrow()
  })
})
