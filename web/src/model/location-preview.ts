import { createMockEstimate } from './estimate'
import { mockResponse } from './fixture'
import type { EstimateRequest, EstimateResponse, MockEconomicInputs } from './contract'
import type { SourcedValue } from './types'

/** Keep new catalog selections usable while awaiting HTTP or working offline.
 * This reuses the existing generic fixture, never invents a zone-specific model.
 * All fixture arithmetic is unchanged and all response blocks remain mock-sourced.
 */
export function createLocationPreview(
  request: EstimateRequest,
  economics: Partial<MockEconomicInputs> = {},
  decisionPolicy?: SourcedValue,
) {
  if (mockResponse.locations.some(location => location.id === request.location_id)) {
    return createMockEstimate(request, economics, decisionPolicy)
  }
  const preview = createMockEstimate({ ...request, location_id: 'SPP_SYSTEM' }, economics, decisionPolicy)
  preview.inputs_echo = { ...request }
  const explanation = `; placeholder preview for requested_location_id=${encodeURIComponent(request.location_id)}; `
    + 'uses the SPP_SYSTEM mock fixture, not fitted exposure for this zone'
  preview.modeled_exposure.source.ref += explanation
  preview.economics.source.ref += explanation
  return preview
}

/**
 * The real response to build both the displayed result and its sensitivity baseline
 * from: the live one matching every current input, or (while a fresh debounced request
 * for the same location/term/economics is still in flight) the last real one whose
 * inputs differ only by site_exposure. Requiring every other field to match is what
 * lets rescaleModeledExposure below stay exact: api/economics.py's other terms
 * (load_mw, flexibility_split, vpp_solar_homes, gpus_per_mw) are held fixed by the
 * caller, so only site_exposure is left to scale. Returns undefined when no
 * compatible real data exists yet, so callers fall back to the placeholder preview.
 */
export function compatibleRealBaseline(
  response: EstimateResponse | undefined,
  lastResponse: EstimateResponse | undefined,
  request: EstimateRequest,
): EstimateResponse | undefined {
  if (response) return response
  if (lastResponse) {
    const prior = lastResponse.inputs_echo
    if (prior.location_id === request.location_id && prior.load_mw === request.load_mw
      && prior.term_years === request.term_years && prior.flexibility_split === request.flexibility_split
      && (prior.vpp_solar_homes ?? 0) === (request.vpp_solar_homes ?? 0)) {
      return lastResponse
    }
  }
  return undefined
}

/**
 * Instantly and exactly rescale a real, previously returned estimate to a new
 * site_exposure while a fresh request for the same inputs is still in flight (e.g.
 * mid-drag on the exposure slider). api/estimate.py applies site_exposure as a pure
 * per-quantile multiplier over precomputed by-year hours, and api/economics.py's
 * gpu-hour/cost/VPP-revenue terms are each themselves a pure multiple of that same
 * modeled exposure (load_mw, flexibility_split, gpus_per_mw, vpp_solar_homes held
 * fixed - see compatibleRealBaseline above) - so this reuses the last real response's
 * own baseline instead of ever substituting a different, unrelated location's
 * placeholder numbers, and stays exact, not an approximation. value_of_early_connection
 * and breakeven_exposure_hours_per_year do not depend on exposure and are carried
 * through unchanged; decision is recomputed from the rescaled costs using the same
 * thresholds api/economics.py and sensitivity.ts both use, so it can never disagree
 * with either.
 */
export function rescaleModeledExposure(response: EstimateResponse, siteExposure: number, decisionTolerance: number): EstimateResponse {
  const priorExposure = response.inputs_echo.site_exposure
  if (!(priorExposure > 0)) return response
  const ratio = siteExposure / priorExposure
  const { modeled_exposure, economics } = response
  const scaleQuantiles = (q: typeof economics.annual_cost_usd) => ({ p50: q.p50 * ratio, p90: q.p90 * ratio, p99: q.p99 * ratio })
  const annualCostUsd = scaleQuantiles(economics.annual_cost_usd)
  const benefit = economics.value_of_early_connection_usd
  const termYears = response.inputs_echo.term_years
  // Same grouping as api/economics.py's decision and sensitivity.ts's snapshot().
  const decision = annualCostUsd.p50 * termYears > benefit * (1 + decisionTolerance)
    ? 'not_worth_it'
    : annualCostUsd.p90 * termYears < benefit * (1 - decisionTolerance) ? 'worth_it' : 'close_call'
  // Update the ref's own embedded site_exposure=<value> query fragment in place
  // (the same fragment sensitivity.ts's exposureIdentity() already normalizes away
  // for comparison) instead of appending new text: that keeps this response's ref
  // and a sibling rescale of the same source to a different target (e.g. the
  // exposure=1 sensitivity baseline alongside the live-exposure result) exactly
  // equal after normalization, and leaves the ref byte-identical to the original
  // whenever siteExposure happens to already match priorExposure.
  const retag = (ref: string) => ref.replace(/([?&])site_exposure=[^&#;\s]*/g, `$1site_exposure=${siteExposure}`)
  return {
    ...response,
    inputs_echo: { ...response.inputs_echo, site_exposure: siteExposure },
    modeled_exposure: {
      ...modeled_exposure,
      p50: modeled_exposure.p50 * ratio,
      p90: modeled_exposure.p90 * ratio,
      p99: modeled_exposure.p99 * ratio,
      worst_contiguous_outage_hours: modeled_exposure.worst_contiguous_outage_hours * ratio,
      by_year: modeled_exposure.by_year.map(row => ({ ...row, p50: row.p50 * ratio, p90: row.p90 * ratio, p99: row.p99 * ratio })),
      source: { ...modeled_exposure.source, ref: retag(modeled_exposure.source.ref) },
    },
    economics: {
      ...economics,
      lost_gpu_hours_per_year: scaleQuantiles(economics.lost_gpu_hours_per_year),
      annual_cost_usd: annualCostUsd,
      vpp_arbitrage_revenue_usd_per_year: scaleQuantiles(economics.vpp_arbitrage_revenue_usd_per_year),
      decision,
      source: { ...economics.source, ref: retag(economics.source.ref) },
    },
  }
}
