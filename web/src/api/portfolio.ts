import { apiUrl, validateEstimateResponse } from './client'
import type { EstimateRequest, EstimateResponse } from '../model/contract'
import type { Source } from '../model'

export interface Thresholds {
  exposure_p90_hours_above: number | null
  confidence_score_below: number | null
}
type Datum = Source & { value: number }
export interface PortfolioSite {
  id: string
  name: string
  inputs: EstimateRequest
  input_source: Source
  thresholds: Thresholds
  ranking: { status: 'available' | 'unavailable'; zone_rank: number | null; composite_score: number | null; reasons: string[]; source: Source }
  wind_evidence: null | {
    reference_location_id: string; period_start_utc: string; period_end_exclusive_utc: string
    proxy_hours: Datum; evaluable_hours: Datum; unknown_hours: Datum
  }
  estimate: EstimateResponse | null
  estimate_unavailable_reason: string | null
  threshold_status: {
    mode: 'static_precomputed_check'
    status: 'not_configured' | 'breached' | 'within_thresholds' | 'unavailable'
    checks: Array<{
      metric: keyof Thresholds; threshold: number; threshold_source: Source
      observed: number | null; observed_source: Source | null
      status: 'breached' | 'within_threshold' | 'unavailable'; reason: string
    }>
  }
}
export interface Portfolio {
  id: string; checked_at_utc: string; storage: string; check_mode: string
  economics_basis: string; ranking_basis: string; sites: PortfolioSite[]
}

export class PortfolioError extends Error {
  constructor(message: string, readonly status?: number) { super(message) }
}

/** Reject malformed or contradictory availability instead of rendering bogus scores. */
export function validatePortfolio(raw: unknown): Portfolio {
  const fail = (): never => { throw new PortfolioError('Invalid portfolio response; comparison unavailable.') }
  const obj = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : fail()
  const str = (v: unknown) => typeof v === 'string' && v.trim() ? v : fail()
  const num = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : fail()
  const src = (v: unknown) => { const s = obj(v); str(s.ref); if (!['assumption', 'model', 'data', 'clause'].includes(String(s.source_type))) fail() }
  const p = obj(raw)
  for (const key of ['id', 'checked_at_utc', 'storage', 'check_mode', 'economics_basis', 'ranking_basis']) str(p[key])
  if (!Array.isArray(p.sites)) fail()
  const ids = new Set<string>()
  for (const value of p.sites as unknown[]) {
    const s = obj(value); const id = str(s.id); str(s.name); src(s.input_source)
    if (ids.has(id)) fail(); ids.add(id)
    const input = obj(s.inputs); str(input.location_id)
    for (const key of ['load_mw', 'term_years', 'flexibility_split', 'site_exposure', 'vpp_solar_homes']) num(input[key])
    if (Number(input.load_mw) <= 0 || !Number.isInteger(input.term_years) || Number(input.term_years) < 1 || Number(input.term_years) > 7 || Number(input.flexibility_split) > 1 || Number(input.site_exposure) > 1) fail()
    const thresholds = obj(s.thresholds)
    for (const key of ['exposure_p90_hours_above', 'confidence_score_below']) if (thresholds[key] !== null) num(thresholds[key])
    if (Number(thresholds.confidence_score_below) > 1) fail()
    const rank = obj(s.ranking); src(rank.source)
    if (!Array.isArray(rank.reasons) || !rank.reasons.every(r => typeof r === 'string')) fail()
    if (rank.status === 'available') { num(rank.composite_score); num(rank.zone_rank); if (!Number.isInteger(rank.zone_rank) || Number(rank.zone_rank) < 1 || (rank.reasons as unknown[]).length) fail() }
    else if (rank.status !== 'unavailable' || rank.composite_score !== null || rank.zone_rank !== null || !(rank.reasons as unknown[]).length) fail()
    if (s.estimate !== null) {
      const estimate = validateEstimateResponse(s.estimate, input as unknown as EstimateRequest)
      if (!['model', 'data'].includes(estimate.modeled_exposure.source.source_type) || /mock:|placeholder|not wired/i.test(estimate.modeled_exposure.source.ref)) fail()
    }
    else str(s.estimate_unavailable_reason)
    if (s.wind_evidence !== null) {
      const w = obj(s.wind_evidence)
      for (const key of ['reference_location_id', 'period_start_utc', 'period_end_exclusive_utc']) str(w[key])
      for (const key of ['proxy_hours', 'evaluable_hours', 'unknown_hours']) { const d = obj(w[key]); num(d.value); src(d) }
    }
    const status = obj(s.threshold_status)
    if (status.mode !== 'static_precomputed_check' || !Array.isArray(status.checks) || !['not_configured', 'breached', 'within_thresholds', 'unavailable'].includes(String(status.status))) fail()
    const checks = status.checks as unknown[]
    const metrics = new Set<string>(); const states: string[] = []
    for (const item of checks) {
      const c = obj(item); const metric = str(c.metric); str(c.reason); num(c.threshold); src(c.threshold_source)
      if (!['exposure_p90_hours_above', 'confidence_score_below'].includes(metric) || metrics.has(metric) || c.threshold !== thresholds[metric]) fail()
      metrics.add(metric); states.push(str(c.status))
      if (c.status === 'unavailable') { if (c.observed !== null || c.observed_source !== null) fail() }
      else {
        num(c.observed); src(c.observed_source)
        const source = obj(c.observed_source)
        if (!['model', 'data'].includes(String(source.source_type)) || /mock:|placeholder|not wired/i.test(str(source.ref))) fail()
        if (s.estimate === null) fail()
        const estimate = s.estimate as EstimateResponse
        const block = metric === 'exposure_p90_hours_above' ? estimate.modeled_exposure : estimate.confidence
        const observed = metric === 'exposure_p90_hours_above' ? estimate.modeled_exposure.p90 : estimate.confidence.score
        if (c.observed !== observed || source.ref !== block.source.ref || source.source_type !== block.source.source_type) fail()
        const breached = metric === 'exposure_p90_hours_above' ? Number(c.observed) > Number(c.threshold) : Number(c.observed) < Number(c.threshold)
        if (c.status !== (breached ? 'breached' : 'within_threshold')) fail()
      }
    }
    if (Object.values(thresholds).filter(v => v !== null).length !== metrics.size) fail()
    const expected = !checks.length ? 'not_configured' : states.includes('breached') ? 'breached' : states.includes('unavailable') ? 'unavailable' : 'within_thresholds'
    if (status.status !== expected) fail()
  }
  return raw as Portfolio
}

export async function portfolioRequest(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<Portfolio> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = setTimeout(abort, 10000)
  try {
  const response = await fetch(apiUrl(`/api/portfolios${path}`), {
    method, signal: controller.signal, headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    let detail = `Portfolio request failed (HTTP ${response.status}).`
    try { const data = await response.json(); if (typeof data.detail === 'string') detail = data.detail } catch { /* Preserve HTTP error. */ }
    throw new PortfolioError(detail, response.status)
  }
  return validatePortfolio(await response.json())
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) throw new PortfolioError('Portfolio request timed out. Refresh evidence before retrying a save; it may already have reached the server.')
    throw error
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}
