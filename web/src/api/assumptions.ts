import { apiUrl, EstimateClientError, type EstimateClientOptions } from './client'
import type { SourcedValue } from '../model/types'
import type { EstimateResponse } from '../model/contract'

export interface EconomicAssumption extends SourcedValue {
  source_type: 'data' | 'assumption'
  unit: string
  source_url: string | null
  retrieved_on: string | null
  low: number
  high: number
}

const units = {
  gpu_rental_price_usd_per_hour: 'USD/GPU-hour',
  industrial_electricity_price_usd_per_mwh: 'USD/MWh',
  gpus_per_mw: 'GPU/MW',
  early_connection_years: 'year',
  early_margin_usd_per_mw_year: 'USD/MW-year',
  close_call_fraction: 'fraction',
  vpp_battery_discharge_mw_per_home: 'MW/home',
  vpp_arbitrage_revenue_usd_per_mwh: 'USD/MWh',
} as const

export type EconomicsAssumptions = {
  schema_version: 1
  status: 'placeholder' | 'mixed' | 'sourced'
} & Record<keyof typeof units, EconomicAssumption>

/** Companion metadata keeps displayed controls aligned with server calculations. */
export function validateEconomicsAssumptions(raw: unknown): EconomicsAssumptions {
  const reject = (path: string): never => {
    throw new EstimateClientError(`Invalid economics assumptions: ${path}.`, {
      code: 'invalid_response', path,
    })
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) reject('response')
  const data = raw as Record<string, unknown>
  const fields = ['schema_version', 'status', ...Object.keys(units)]
  if (Object.keys(data).some(key => !fields.includes(key))) reject('unknown field')
  if (data.schema_version !== 1) reject('schema_version')
  if (!['placeholder', 'mixed', 'sourced'].includes(data.status as string)) reject('status')
  const placeholders: boolean[] = []
  for (const [key, unit] of Object.entries(units)) {
    const entry = data[key] as EconomicAssumption | undefined
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) reject(key)
    const item = entry!
    if (Object.keys(item).some(field => !['value', 'source_type', 'ref', 'unit', 'source_url', 'retrieved_on', 'low', 'high'].includes(field))) reject(`${key}.unknown field`)
    if (!['data', 'assumption'].includes(item.source_type)) reject(`${key}.source_type`)
    if (typeof item.ref !== 'string' || !item.ref.trim()) reject(`${key}.ref`)
    if (item.unit !== unit) reject(`${key}.unit`)
    for (const field of ['value', 'low', 'high'] as const) {
      if (typeof item[field] !== 'number' || !Number.isFinite(item[field]) || item[field] < 0) reject(`${key}.${field}`)
    }
    if (item.low > item.value || item.value > item.high) reject(`${key}.range`)
    if (item.source_url !== null && (typeof item.source_url !== 'string' || !item.source_url.startsWith('https://'))) reject(`${key}.source_url`)
    if (item.retrieved_on !== null && (typeof item.retrieved_on !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(item.retrieved_on))) reject(`${key}.retrieved_on`)
    const placeholder = /^mock:/i.test(item.ref) || /placeholder/i.test(item.ref)
    placeholders.push(placeholder)
    if (placeholder) {
      if (item.source_type !== 'assumption' || !item.ref.startsWith('mock://') || !/placeholder/i.test(item.ref)) reject(`${key}.placeholder provenance`)
      if (item.source_url !== null || item.retrieved_on !== null) reject(`${key}.placeholder retrieval`)
    } else {
      if (!item.source_url || !item.retrieved_on) reject(`${key}.missing retrieval`)
      try {
        const url = new URL(item.source_url!)
        if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) reject(`${key}.source_url`)
      } catch { reject(`${key}.source_url`) }
      const date = new Date(`${item.retrieved_on}T00:00:00Z`)
      if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== item.retrieved_on) reject(`${key}.retrieved_on`)
    }
  }
  if ((data.gpus_per_mw as EconomicAssumption).value <= 0) reject('gpus_per_mw.value')
  if ((data.close_call_fraction as EconomicAssumption).value >= 1) reject('close_call_fraction.value')
  const status = placeholders.every(Boolean) ? 'placeholder' : placeholders.some(Boolean) ? 'mixed' : 'sourced'
  if (data.status !== status) reject('status disagrees with provenance')
  return raw as EconomicsAssumptions
}

export async function getEconomicsAssumptions({ signal, fetchImpl = globalThis.fetch }: EstimateClientOptions = {}): Promise<EconomicsAssumptions> {
  const response = await fetchImpl(apiUrl('/api/economics-assumptions'), {
    method: 'GET', headers: { Accept: 'application/json' }, signal,
  })
  if (!response.ok) {
    throw new EstimateClientError(`Economic assumptions request failed (HTTP ${response.status}).`, {
      code: 'http_error', status: response.status,
    })
  }
  return validateEconomicsAssumptions(await response.json())
}

/** Reject a metadata/estimate race rather than displaying controls from another revision. */
export function validateEconomicConsistency(response: EstimateResponse, assumptions: EconomicsAssumptions): void {
  const request = response.inputs_echo
  const economics = response.economics
  const reject = (path: string): never => {
    throw new EstimateClientError(`Estimate and economics assumptions differ at ${path}.`, {
      code: 'invalid_response', path: `economics.${path}`,
    })
  }
  const match = (actual: number, expected: number, path: string) => {
    if (!Number.isFinite(expected) || Math.abs(actual - expected) > Math.max(1, Math.abs(expected)) * 1e-10) reject(path)
  }
  const density = assumptions.gpus_per_mw.value
  const rental = assumptions.gpu_rental_price_usd_per_hour.value
  const interruptibleMw = request.load_mw * request.flexibility_split
  match(economics.gpus_per_mw, density, 'gpus_per_mw')
  for (const quantile of ['p50', 'p90', 'p99'] as const) {
    const gpuHours = response.modeled_exposure[quantile] * interruptibleMw * density
    match(economics.lost_gpu_hours_per_year[quantile], gpuHours, `lost_gpu_hours_per_year.${quantile}`)
    match(economics.annual_cost_usd[quantile], gpuHours * rental, `annual_cost_usd.${quantile}`)
  }
  const benefit = Math.min(assumptions.early_connection_years.value, request.term_years)
    * request.load_mw * assumptions.early_margin_usd_per_mw_year.value
  match(economics.value_of_early_connection_usd, benefit, 'value_of_early_connection_usd')
  const costPerHour = interruptibleMw * density * rental
  if (costPerHour === 0) {
    if (economics.breakeven_exposure_hours_per_year !== null) reject('breakeven_exposure_hours_per_year')
  } else {
    if (economics.breakeven_exposure_hours_per_year === null) reject('breakeven_exposure_hours_per_year')
    match(economics.breakeven_exposure_hours_per_year!, benefit / (request.term_years * costPerHour), 'breakeven_exposure_hours_per_year')
  }
  const tolerance = assumptions.close_call_fraction.value
  const decision = economics.annual_cost_usd.p50 * request.term_years > benefit * (1 + tolerance)
    ? 'not_worth_it'
    : economics.annual_cost_usd.p90 * request.term_years < benefit * (1 - tolerance) ? 'worth_it' : 'close_call'
  if (economics.decision !== decision) reject('decision')
}
