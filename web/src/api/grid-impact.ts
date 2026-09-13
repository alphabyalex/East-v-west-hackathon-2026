import { apiUrl, type EstimateClientOptions } from './client'
import type { Source, SourcedValue } from '../model'

export type GridDatum = Source & { value: number | null }
export type PowerBin = Record<'month' | 'hour_utc' | 'proxy_hours' | 'unknown_hours' | 'usd_per_available_mw', SourcedValue>
export interface PowerEvidence {
  status: 'observed_hours_only'; source_location_id: string; basis: string
  reference_price_usd_mwh: SourcedValue; proxy_hours: SourcedValue
  unknown_hours: SourcedValue; usd_per_available_mw: SourcedValue; bins: PowerBin[]
}
export interface GridImpact {
  schema_version: 'grid-impact-v1'; location_id: string
  coverage: { wind: { status: string; reason?: string; period_start_utc?: string; period_end_exclusive_utc?: string } }
  location_mapping?: { source_location_id: string; scope: string }
  evidence: Record<string, unknown>
  wind_absorption_mwh_in_observed_hours: GridDatum
  carbon_absorbed_tonnes_in_observed_hours: GridDatum
  carbon_shifted_tonnes_in_observed_hours: GridDatum
  cheap_power: PowerEvidence | { status: 'unavailable'; reason: string; bins: [] }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid grid-impact object')
  return value as Record<string, unknown>
}
function datum(value: unknown, nullable = false): asserts value is GridDatum {
  const item = record(value)
  if (!(nullable && item.value === null) && (typeof item.value !== 'number' || !Number.isFinite(item.value))) throw new Error('Invalid grid-impact number')
  if (!['data', 'model', 'assumption', 'clause'].includes(String(item.source_type)) || typeof item.ref !== 'string' || !item.ref.trim()) throw new Error('Missing grid-impact source')
}

/** No repair/default numbers at the network boundary. Older servers can supply
 * wind/carbon without the additive price field; price remains unavailable. */
export function validateGridImpact(raw: unknown, location: string): GridImpact {
  const body = record(raw)
  if (body.schema_version !== 'grid-impact-v1' || body.location_id !== location) throw new Error('Grid-impact identity mismatch')
  record(body.evidence)
  const coverage = record(record(body.coverage).wind)
  if (!['unavailable', 'partial_period', 'complete_calendar_year'].includes(String(coverage.status))) throw new Error('Invalid grid-impact coverage')
  for (const field of ['wind_absorption_mwh_in_observed_hours', 'carbon_absorbed_tonnes_in_observed_hours', 'carbon_shifted_tonnes_in_observed_hours']) datum(body[field], true)
  if (body.location_mapping) {
    const mapping = record(body.location_mapping)
    if (typeof mapping.source_location_id !== 'string' || typeof mapping.scope !== 'string') throw new Error('Invalid grid-impact mapping')
  }
  const power = record(body.cheap_power ?? { status: 'unavailable', reason: 'Hourly price evidence not yet supplied', bins: [] })
  if (power.status === 'observed_hours_only') {
    if (typeof power.source_location_id !== 'string' || typeof power.basis !== 'string' || !power.basis.trim()
      || power.source_location_id !== (body.location_mapping ? record(body.location_mapping).source_location_id : location)) throw new Error('Price evidence identity mismatch')
    for (const key of ['proxy_hours', 'unknown_hours', 'usd_per_available_mw', 'reference_price_usd_mwh']) datum(power[key])
    if ((power.reference_price_usd_mwh as SourcedValue).value !== 0 || !Array.isArray(power.bins) || power.bins.length !== 288) throw new Error('Invalid price grid')
    const totals = { proxy_hours: 0, unknown_hours: 0, usd_per_available_mw: 0 }
    power.bins.forEach((rawBin, index) => {
      const bin = record(rawBin)
      for (const key of ['month', 'hour_utc', ...Object.keys(totals)]) {
        datum(bin[key])
        if ((bin[key] as SourcedValue).value < 0) throw new Error('Negative price aggregate')
      }
      const row = bin as PowerBin
      if (row.month.value !== Math.floor(index / 24) + 1 || row.hour_utc.value !== index % 24
        || !Number.isInteger(row.proxy_hours.value) || !Number.isInteger(row.unknown_hours.value)
        || row.proxy_hours.value + row.unknown_hours.value > 31
        || (row.proxy_hours.value === 0 && row.usd_per_available_mw.value !== 0)) throw new Error('Invalid price bin')
      for (const key of Object.keys(totals) as (keyof typeof totals)[]) totals[key] += row[key].value
    })
    for (const key of Object.keys(totals) as (keyof typeof totals)[]) {
      if (Math.abs(totals[key] - (power[key] as SourcedValue).value) > 1e-8 * Math.max(1, totals[key])) throw new Error('Price bins do not reconcile')
    }
  } else if (power.status !== 'unavailable' || typeof power.reason !== 'string') throw new Error('Invalid price availability')
  return { ...body, cheap_power: power } as unknown as GridImpact
}

export async function getGridImpact(location: string, { signal, fetchImpl = globalThis.fetch }: EstimateClientOptions = {}) {
  const response = await fetchImpl(apiUrl(`/api/grid-impact/${encodeURIComponent(location)}`), { headers: { Accept: 'application/json' }, cache: 'no-store', signal })
  if (!response.ok) throw new Error(`Grid-impact data unavailable (HTTP ${response.status}).`)
  return validateGridImpact(await response.json(), location)
}

export function powerScenario(power: PowerEvidence, load: number, available: number) {
  if (!Number.isFinite(load) || load <= 0 || !Number.isFinite(available) || available < 0 || available > 1) throw new Error('Invalid upward-capacity scenario')
  const scale = load * available
  const calculate = (input: SourcedValue, unit: string): SourcedValue => {
    const value = input.value * scale
    if (!Number.isFinite(value)) throw new Error('Grid-impact scenario exceeds numeric range')
    return { value, source_type: 'assumption', ref: `${input.ref}; scenario://grid-impact; selected_load_mw=${load}; upward_available_fraction=${available}; ${unit}=${input.value}*${load}*${available}; assumed accessible capacity and wholesale price pass-through, not measured savings or recoverable wind` }
  }
  return { wind: calculate(power.proxy_hours, 'MWh'), dollars: calculate(power.usd_per_available_mw, 'USD'),
    bins: power.bins.map(bin => ({ ...bin, wind: calculate(bin.proxy_hours, 'MWh'), dollars: calculate(bin.usd_per_available_mw, 'USD') })) }
}
