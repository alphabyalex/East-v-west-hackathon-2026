import type { BaselineYear, SourcedValue } from '../../model'

export const QUANTILES = [10, 50, 90, 99] as const
export type SurfaceQuantile = typeof QUANTILES[number]

export const SURFACE_SIZE = { width: 10, height: 5, depth: 6 } as const

export interface SurfaceSample {
  yearIndex: number
  quantile: SurfaceQuantile
  year: SourcedValue
  exposure: SourcedValue
  position: [number, number, number]
}

/**
 * Each vertex represents one supplied yearly quantile, retaining its provenance.
 * Triangles only connect those observations for display; they do not add modeled
 * values or imply a probability density between the supplied quantiles.
 */
export function buildSurfaceData(rows: BaselineYear[], maximumHours: number) {
  if (!Number.isFinite(maximumHours) || maximumHours <= 0) {
    throw new RangeError('maximumHours must be a finite positive number.')
  }
  if (rows.length < 1 || rows.length > 20) {
    throw new RangeError('The exposure surface requires between 1 and 20 yearly rows.')
  }

  let previousYear = -Infinity
  for (const row of rows) {
    if (!Number.isFinite(row.year.value) || row.year.value <= previousYear) {
      throw new RangeError('Exposure surface years must be finite and strictly increasing.')
    }
    previousYear = row.year.value
    let previousExposure = 0
    for (const quantile of QUANTILES) {
      const exposure = row[`p${quantile}`].value
      if (!Number.isFinite(exposure) || exposure < previousExposure) {
        throw new RangeError('Exposure quantiles must be finite, nonnegative, and ordered.')
      }
      previousExposure = exposure
    }
  }

  const firstYear = rows[0].year.value
  const yearSpan = rows[rows.length - 1].year.value - firstYear
  const positions = new Float32Array(rows.length * QUANTILES.length * 3)
  const indices = new Uint32Array((rows.length - 1) * (QUANTILES.length - 1) * 6)
  const samples: SurfaceSample[] = []

  rows.forEach((row, yearIndex) => {
    const x = yearSpan === 0
      ? 0
      : -SURFACE_SIZE.width / 2 + (row.year.value - firstYear) / yearSpan * SURFACE_SIZE.width

    QUANTILES.forEach((quantile, quantileIndex) => {
      const exposure = row[`p${quantile}`]
      const position: [number, number, number] = [
        x,
        exposure.value / maximumHours * SURFACE_SIZE.height,
        SURFACE_SIZE.depth / 2 - (quantile - 10) / 89 * SURFACE_SIZE.depth,
      ]
      positions.set(position, (yearIndex * QUANTILES.length + quantileIndex) * 3)
      samples.push({ yearIndex, quantile, year: row.year, exposure, position })
    })
  })

  let offset = 0
  for (let yearIndex = 0; yearIndex < rows.length - 1; yearIndex += 1) {
    for (let quantileIndex = 0; quantileIndex < QUANTILES.length - 1; quantileIndex += 1) {
      const a = yearIndex * QUANTILES.length + quantileIndex
      const b = a + QUANTILES.length
      const c = a + 1
      const d = b + 1
      indices.set([a, b, c, b, d, c], offset)
      offset += 6
    }
  }

  return { positions, indices, samples }
}
