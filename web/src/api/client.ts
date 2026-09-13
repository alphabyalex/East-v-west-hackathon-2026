import type { EstimateRequest, EstimateResponse } from '../model/contract'

export class EstimateClientError extends Error {
  readonly code: 'invalid_request' | 'invalid_response' | 'http_error'
  readonly path?: string
  readonly status?: number

  constructor(message: string, details: {
    code: 'invalid_request' | 'invalid_response' | 'http_error'
    path?: string
    status?: number
  }) {
    super(message)
    this.name = 'EstimateClientError'
    this.code = details.code
    this.path = details.path
    this.status = details.status
  }
}

function invalid(path: string, requirement: string): never {
  throw new EstimateClientError(`Invalid estimate response: ${path} ${requirement}.`, {
    code: 'invalid_response', path,
  })
}

function object(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    invalid(path, 'must be an object')
  }
  return value as Record<string, unknown>
}

function nonemptyString(value: unknown, path: string): asserts value is string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    invalid(path, 'must be a nonempty string')
  }
}

function number(value: unknown, path: string): asserts value is number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    invalid(path, 'must be a finite nonnegative number')
  }
}

function fraction(value: unknown, path: string): asserts value is number {
  number(value, path)
  if (value > 1) invalid(path, 'must be between 0 and 1')
}

function source(value: unknown, path: string) {
  const provenance = object(value, path)
  if (!['model', 'data', 'clause', 'assumption'].includes(provenance.source_type as string)) {
    invalid(`${path}.source_type`, 'must be model, data, clause, or assumption')
  }
  nonemptyString(provenance.ref, `${path}.ref`)
}

function quantiles(value: unknown, path: string) {
  const row = object(value, path)
  number(row.p50, `${path}.p50`)
  number(row.p90, `${path}.p90`)
  number(row.p99, `${path}.p99`)
  if (row.p50 > row.p90 || row.p90 > row.p99) {
    invalid(path, 'must have p50 <= p90 <= p99')
  }
  return row
}

const inputFields = ['location_id', 'load_mw', 'term_years', 'flexibility_split', 'site_exposure', 'vpp_solar_homes'] as const

/** Reject before JSON serialization can turn non-finite input into null. */
function validateRequest(request: EstimateRequest) {
  function reject(field: string, requirement: string): never {
    throw new EstimateClientError(`Invalid estimate request: ${field} ${requirement}.`, {
      code: 'invalid_request', path: field,
    })
  }
  if (!request || typeof request !== 'object') reject('request', 'must be an object')
  if (typeof request.location_id !== 'string' || request.location_id.trim().length === 0) {
    reject('location_id', 'must be a nonempty string')
  }
  for (const field of ['load_mw', 'term_years', 'flexibility_split', 'site_exposure', 'vpp_solar_homes'] as const) {
    if (typeof request[field] !== 'number' || !Number.isFinite(request[field] as number)) {
      reject(field, 'must be a finite number')
    }
  }
  if (request.load_mw <= 0) reject('load_mw', 'must be positive')
  if (request.vpp_solar_homes !== undefined && (request.vpp_solar_homes < 0 || !Number.isInteger(request.vpp_solar_homes))) {
    reject('vpp_solar_homes', 'must be a nonnegative integer')
  }
  if (!Number.isInteger(request.term_years) || request.term_years < 1 || request.term_years > 7) {
    reject('term_years', 'must be an integer between 1 and 7')
  }
  for (const field of ['flexibility_split', 'site_exposure'] as const) {
    if (request[field] < 0 || request[field] > 1) reject(field, 'must be between 0 and 1')
  }
}

/** Validate at the transport boundary; never repair, rescale, or add provenance. */
export function validateEstimateResponse(raw: unknown, request?: EstimateRequest): EstimateResponse {
  const response = object(raw, 'response')
  const echo = object(response.inputs_echo, 'inputs_echo')
  nonemptyString(echo.location_id, 'inputs_echo.location_id')
  number(echo.load_mw, 'inputs_echo.load_mw')
  if (echo.load_mw === 0) invalid('inputs_echo.load_mw', 'must be positive')
  number(echo.term_years, 'inputs_echo.term_years')
  if (!Number.isInteger(echo.term_years) || echo.term_years < 1 || echo.term_years > 7) {
    invalid('inputs_echo.term_years', 'must be an integer between 1 and 7')
  }
  fraction(echo.flexibility_split, 'inputs_echo.flexibility_split')
  fraction(echo.site_exposure, 'inputs_echo.site_exposure')
  if (echo.vpp_solar_homes !== undefined) number(echo.vpp_solar_homes, 'inputs_echo.vpp_solar_homes')
  if (request) {
    for (const field of inputFields) {
      const echoVal = echo[field] ?? (field === 'vpp_solar_homes' ? 0 : undefined);
      const reqVal = request[field] ?? (field === 'vpp_solar_homes' ? 0 : undefined);
      if (echoVal !== reqVal) invalid(`inputs_echo.${field}`, 'must match the submitted request')
    }
  }

  const exposure = quantiles(response.modeled_exposure, 'modeled_exposure')
  if (exposure.unit !== 'hours/year') invalid('modeled_exposure.unit', 'must be hours/year')
  number(exposure.worst_contiguous_outage_hours, 'modeled_exposure.worst_contiguous_outage_hours')
  source(exposure.source, 'modeled_exposure.source')
  if (!Array.isArray(exposure.by_year) || exposure.by_year.length === 0) {
    invalid('modeled_exposure.by_year', 'must be a nonempty array')
  }
  const termYears = echo.term_years
  // A partial or reordered horizon would silently change the displayed contract.
  if (exposure.by_year.length !== termYears) {
    invalid('modeled_exposure.by_year', 'must contain the complete contract term')
  }
  const years = new Set<number>()
  exposure.by_year.forEach((value, index) => {
    const path = `modeled_exposure.by_year[${index}]`
    const row = quantiles(value, path)
    number(row.year, `${path}.year`)
    if (!Number.isInteger(row.year) || row.year < 1 || row.year > termYears) {
      invalid(`${path}.year`, 'must be an integer within the echoed contract term')
    }
    if (years.has(row.year)) invalid(`${path}.year`, 'must not repeat a contract year')
    if (row.year !== index + 1) invalid(`${path}.year`, 'must follow ascending contract-year order')
    years.add(row.year)
  })

  const confidence = object(response.confidence, 'confidence')
  if (!['High', 'Medium', 'Low'].includes(confidence.level as string)) {
    invalid('confidence.level', 'must be High, Medium, or Low')
  }
  fraction(confidence.score, 'confidence.score')
  nonemptyString(confidence.basis, 'confidence.basis')
  source(confidence.source, 'confidence.source')

  const economics = object(response.economics, 'economics')
  number(economics.gpus_per_mw, 'economics.gpus_per_mw')
  quantiles(economics.lost_gpu_hours_per_year, 'economics.lost_gpu_hours_per_year')
  quantiles(economics.annual_cost_usd, 'economics.annual_cost_usd')
  number(economics.value_of_early_connection_usd, 'economics.value_of_early_connection_usd')
  if (economics.breakeven_exposure_hours_per_year !== null) {
    number(economics.breakeven_exposure_hours_per_year, 'economics.breakeven_exposure_hours_per_year')
  }
  if (!['worth_it', 'not_worth_it', 'close_call'].includes(economics.decision as string)) {
    invalid('economics.decision', 'must be worth_it, not_worth_it, or close_call')
  }
  source(economics.source, 'economics.source')

  const tariff = object(response.tariff, 'tariff')
  if (tariff.operator !== 'SPP') invalid('tariff.operator', 'must be SPP')
  nonemptyString(tariff.service, 'tariff.service')
  if (!Array.isArray(tariff.curtailment_triggers)) invalid('tariff.curtailment_triggers', 'must be an array')
  tariff.curtailment_triggers.forEach((value, index) => {
    const path = `tariff.curtailment_triggers[${index}]`
    const trigger = object(value, path)
    nonemptyString(trigger.text, `${path}.text`)
    if (typeof trigger.observable !== 'boolean') invalid(`${path}.observable`, 'must be a boolean')
    source(trigger.source, `${path}.source`)
  })

  return raw as EstimateResponse
}

export interface EstimateClientOptions {
  signal?: AbortSignal
  fetchImpl?: typeof fetch
}

/** An empty override supports same-origin hosting; local development needs no proxy. */
export function apiUrl(path: string): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
  return `${base.replace(/\/+$/, '')}${path}`
}

/** Importing this module never contacts the API. */
export async function postEstimate(
  request: EstimateRequest,
  { signal, fetchImpl = globalThis.fetch }: EstimateClientOptions = {},
): Promise<EstimateResponse> {
  validateRequest(request)
  // Snapshot the five contract fields so later caller edits cannot change echo validation.
  const submitted: EstimateRequest = {
    location_id: request.location_id,
    load_mw: request.load_mw,
    term_years: request.term_years,
    flexibility_split: request.flexibility_split,
    site_exposure: request.site_exposure,
  }
  if (request.vpp_solar_homes !== undefined) {
    submitted.vpp_solar_homes = request.vpp_solar_homes
  }
  const response = await fetchImpl(apiUrl('/api/estimate'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(submitted),
    signal,
  })
  if (!response.ok) {
    throw new EstimateClientError(`Estimate request failed (HTTP ${response.status}).`, {
      code: 'http_error', status: response.status,
    })
  }
  let raw: unknown
  try {
    raw = await response.json()
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new EstimateClientError('Invalid estimate response: expected JSON.', { code: 'invalid_response' })
    }
    throw error
  }
  return validateEstimateResponse(raw, submitted)
}
