import type { EconomicsAssumptions } from '../api/assumptions'
import type { EstimateResponse, Quantiles } from './contract'
import type { ScenarioInputs, Source, SourcedValue } from './types'

export type SensitivityKey = 'gpu_rental_price' | 'electricity_price' | 'utilization' | 'flexibility_split' | 'site_exposure'

export interface SensitivitySnapshot {
  /** Values after the corresponding marginal annual cost path, not net-value quantiles. */
  net_value_usd: Quantiles<SourcedValue>
  early_value_usd: SourcedValue
  annual_cost_usd: Quantiles<SourcedValue>
  decision: EstimateResponse['economics']['decision']
  breakeven_hours: SourcedValue<number | null>
  breakeven_note?: string
  source: Source
}

export interface SensitivityEndpoint {
  input: SourcedValue
  delta_value_usd: SourcedValue
  snapshot: SensitivitySnapshot
  source: Source
}

export interface UnmodeledSensitivityEndpoint {
  input: SourcedValue
  delta_value_usd: null
  snapshot: null
  source: Source
}

interface SensitivityRowBase {
  key: SensitivityKey
  label: string
  unit: 'USD/GPU-hour' | 'USD/MWh' | 'fraction'
  baseline_input: SourcedValue
  source: Source
}

export type SensitivityRow = SensitivityRowBase & ({
  status: 'modeled'
  low: SensitivityEndpoint
  high: SensitivityEndpoint
  swing_usd: SourcedValue
} | {
  status: 'not_modeled'
  reason: string
  low: UnmodeledSensitivityEndpoint
  high: UnmodeledSensitivityEndpoint
  swing_usd: null
})

export interface SensitivityResult {
  baseline: SensitivitySnapshot
  rows: SensitivityRow[]
  source: Source
  notes: string[]
}

const policyRef = 'docs/ASSUMPTIONS.md#sensitivity-policy'
const utilizationRef = 'docs/ASSUMPTIONS.md#3-value-of-connecting-early; documented revenue-earning utilization scenario, not a measured site value or an applied POST input'
const assumption = (value: number, ref: string): SourcedValue => ({ value, source_type: 'assumption', ref })

/** Existing documented planning range; utilization is deliberately not modeled. */
export const defaultUtilizationRange = {
  baseline: assumption(0.7, utilizationRef),
  low: assumption(0.6, utilizationRef),
  high: assumption(0.85, utilizationRef),
}

const fractionRange = {
  low: assumption(0, `${policyRef}; full user-set scenario span, not a statistical interval`),
  high: assumption(1, `${policyRef}; full user-set scenario span, not a statistical interval`),
}
const quantiles = ['p50', 'p90', 'p99'] as const
const mapQuantiles = <T,>(fn: (key: keyof Quantiles) => T): Quantiles<T> => ({ p50: fn('p50'), p90: fn('p90'), p99: fn('p99') })
const isMock = (source: Source) => /^mock:/i.test(source.ref) || /placeholder/i.test(source.ref)
// Only this request field varies; retain any model version/evidence query fields.
const exposureIdentity = (ref: string) => ref.replace(/([?&])site_exposure=[^&#;\s]*/g, '$1site_exposure=<varied>')

function finite(value: number, label: string, minimum = 0) {
  if (!Number.isFinite(value) || value < minimum) throw new RangeError(`Invalid sensitivity ${label}.`)
}

function near(actual: number, expected: number, label: string) {
  if (!Number.isFinite(actual) || !Number.isFinite(expected)
    || Math.abs(actual - expected) > Math.max(1, Math.abs(actual), Math.abs(expected)) * 1e-10) {
    throw new RangeError(`Sensitivity baseline differs at ${label}.`)
  }
}

function sourced<T extends number | null>(value: T, source: Source, field: string): SourcedValue<T> {
  if (value !== null) finite(value, field, -Infinity)
  return { value, source_type: source.source_type, ref: `${source.ref}; field=${field}` }
}

/**
 * Cheap one-at-a-time arithmetic over matching exposure from the same source.
 * A site=1 response (or exact recovery from a nonzero site factor) provides the
 * unscaled baseline. At zero a separate response is required: division cannot recover it.
 * Rental changes gross interruption cost only: early operating-margin value stays
 * fixed, as it does in POST /api/estimate. Unmodeled factors never become zero bars.
 */
export function buildSensitivity(
  response: EstimateResponse,
  exposureAtOne: EstimateResponse,
  inputs: ScenarioInputs,
  assumptions: EconomicsAssumptions,
): SensitivityResult {
  const request = response.inputs_echo
  const rawRequest = exposureAtOne.inputs_echo
  if (rawRequest.site_exposure !== 1) throw new RangeError('Sensitivity requires a site_exposure=1 baseline.')
  for (const key of ['location_id', 'load_mw', 'term_years', 'flexibility_split', 'vpp_solar_homes'] as const) {
    if (request[key] !== rawRequest[key]) throw new RangeError(`Sensitivity baseline request differs at ${key}.`)
  }
  if (inputs.location_id !== request.location_id) throw new RangeError('Sensitivity inputs differ at location_id.')
  near(inputs.load_mw, request.load_mw, 'load_mw')
  near(inputs.contract_years, request.term_years, 'term_years')
  near(inputs.flexibility_percent / 100, request.flexibility_split, 'flexibility_split')
  near(inputs.site_exposure, request.site_exposure, 'site_exposure')
  const exposureSource = response.modeled_exposure.source
  const rawSource = exposureAtOne.modeled_exposure.source
  if (exposureSource.source_type !== rawSource.source_type
    || exposureIdentity(exposureSource.ref) !== exposureIdentity(rawSource.ref)) {
    throw new RangeError('Sensitivity requires matching exposure provenance.')
  }
  const confidence = response.confidence
  const rawConfidence = exposureAtOne.confidence
  if (confidence.level !== rawConfidence.level || confidence.score !== rawConfidence.score
    || confidence.basis !== rawConfidence.basis || confidence.source.source_type !== rawConfidence.source.source_type
    || confidence.source.ref !== rawConfidence.source.ref) {
    throw new RangeError('Sensitivity requires matching confidence evidence.')
  }
  near(response.modeled_exposure.worst_contiguous_outage_hours,
    exposureAtOne.modeled_exposure.worst_contiguous_outage_hours * request.site_exposure, 'worst_contiguous_outage_hours')
  const annualRows = response.modeled_exposure.by_year
  const rawRows = exposureAtOne.modeled_exposure.by_year
  if (annualRows.length !== request.term_years || rawRows.length !== request.term_years) {
    throw new RangeError('Sensitivity baseline must cover the full contract term.')
  }
  annualRows.forEach((row, index) => {
    if (row.year !== index + 1 || rawRows[index].year !== index + 1) {
      throw new RangeError('Sensitivity baseline years must be complete and ordered.')
    }
    quantiles.forEach(key => near(row[key], rawRows[index][key] * request.site_exposure, `by_year/${index}/${key}`))
  })
  near(inputs.vpp_solar_homes, request.vpp_solar_homes ?? 0, 'vpp_solar_homes')
  const vppOffset = (request.vpp_solar_homes ?? 0) * assumptions.vpp_battery_discharge_mw_per_home.value
  const vppRevenuePerHour = vppOffset * assumptions.vpp_arbitrage_revenue_usd_per_mwh.value
  const netLoad = (flexibility: number) => Math.max(0, request.load_mw * flexibility - vppOffset)
  const baseRental = inputs.gpu_hour_value_usd
  const density = response.economics.gpus_per_mw
  const benefit = response.economics.value_of_early_connection_usd
  const tolerance = assumptions.close_call_fraction.value
  for (const [label, value] of Object.entries({ rental: baseRental, density, benefit, tolerance })) finite(value, label)
  if (tolerance >= 1) throw new RangeError('Sensitivity decision tolerance must be less than one.')
  quantiles.forEach(key => {
    near(response.modeled_exposure[key], exposureAtOne.modeled_exposure[key] * request.site_exposure, `exposure/${key}`)
    near(response.economics.annual_cost_usd[key], response.modeled_exposure[key]
      * netLoad(request.flexibility_split) * density * baseRental
      - response.modeled_exposure[key] * vppOffset * assumptions.vpp_arbitrage_revenue_usd_per_mwh.value, `annual_cost/${key}`)
  })

  const refs = [exposureSource, rawSource, response.economics.source,
    assumptions.gpu_rental_price_usd_per_hour, assumptions.industrial_electricity_price_usd_per_mwh,
    assumptions.close_call_fraction, assumptions.vpp_battery_discharge_mw_per_home,
    assumptions.vpp_arbitrage_revenue_usd_per_mwh]
  const source: Source = {
    source_type: 'assumption',
    ref: `${refs.some(isMock) ? 'mock://sensitivity-placeholder' : policyRef}; policy=${policyRef}; `
      + 'one input varies at a time; early operating-margin value held fixed; net interruptible GPU rental loss less VPP arbitrage revenue; VPP homes and assumptions held fixed; '
      + 'full exposure recovered by inverse canonical site scaling when nonzero, otherwise a matching site=1 response; '
      + `scenario=${JSON.stringify(request)}; baseline_rental=${baseRental}; density=${density}; `
      + `early_value=${benefit}; close_call_fraction=${tolerance}; dependencies=[${refs.map(item => item.ref).join(' | ')}]`,
  }

  const snapshot = (annualCost: Quantiles, rental: number, flexibility: number, itemSource: Source): SensitivitySnapshot => {
    // Preserve the API's arithmetic grouping: strict decision thresholds must not
    // drift across equality because a multiplication chain was reassociated.
    const costPerExposureHour = netLoad(flexibility) * density * rental - vppRevenuePerHour
    const denominator = request.term_years * costPerExposureHour
    finite(denominator, 'break-even denominator', -Infinity)
    const breakEven = denominator <= 0 ? null : benefit / denominator
    const net = mapQuantiles(key => sourced(benefit - annualCost[key] * request.term_years, itemSource, `net_value_usd/${key}`))
    const decision = annualCost.p50 * request.term_years > benefit * (1 + tolerance)
      ? 'not_worth_it'
      : annualCost.p90 * request.term_years < benefit * (1 - tolerance) ? 'worth_it' : 'close_call'
    return {
      net_value_usd: net,
      early_value_usd: sourced(benefit, itemSource, 'early_value_usd'),
      annual_cost_usd: mapQuantiles(key => sourced(annualCost[key], itemSource, `annual_cost_usd/${key}`)),
      decision,
      breakeven_hours: sourced(breakEven, itemSource, 'breakeven_hours'),
      ...(breakEven === null ? { breakeven_note: 'No finite crossover: net interruption cost per exposure hour is zero or negative.' } : {}),
      source: itemSource,
    }
  }
  const baseline = snapshot(response.economics.annual_cost_usd, baseRental, request.flexibility_split, source)
  if (baseline.decision !== response.economics.decision) throw new RangeError('Sensitivity decision policy differs from the estimate.')
  const baseBreakEven = response.economics.breakeven_exposure_hours_per_year
  if (baseBreakEven === null || baseline.breakeven_hours.value === null) {
    if (baseBreakEven !== baseline.breakeven_hours.value) throw new RangeError('Sensitivity break-even differs from the estimate.')
  } else near(baseBreakEven, baseline.breakeven_hours.value, 'breakeven_hours')
  // Carry the canonical value exactly rather than introducing a rounding difference.
  baseline.breakeven_hours = sourced(baseBreakEven, source, 'breakeven_hours')

  const range = (key: 'gpu_rental_price_usd_per_hour' | 'industrial_electricity_price_usd_per_mwh') => {
    const item = assumptions[key]
    finite(item.low, `${key}/low`)
    finite(item.high, `${key}/high`)
    if (item.low > item.high) throw new RangeError(`Invalid sensitivity ${key} range.`)
    return {
      low: sourced(item.low, item, `${key}/recorded-scenario-low`),
      high: sourced(item.high, item, `${key}/recorded-scenario-high`),
    }
  }
  const rowSource = (key: SensitivityKey, low: SourcedValue, high: SourcedValue): Source => ({
    source_type: 'assumption',
    ref: `${source.ref}; varied=${key}; range=[${low.value},${high.value}]; range_sources=[${low.ref} | ${high.ref}]`,
  })
  const modeledRow = (
    key: 'gpu_rental_price' | 'flexibility_split' | 'site_exposure', label: string,
    unit: SensitivityRow['unit'], baselineInput: SourcedValue, bounds: { low: SourcedValue; high: SourcedValue },
  ): SensitivityRow => {
    const itemSource = rowSource(key, bounds.low, bounds.high)
    const endpoint = (input: SourcedValue): SensitivityEndpoint => {
      const rental = key === 'gpu_rental_price' ? input.value : baseRental
      const flexibility = key === 'flexibility_split' ? input.value : request.flexibility_split
      const site = key === 'site_exposure' ? input.value : request.site_exposure
      const endpointSource = { ...itemSource, ref: `${itemSource.ref}; input=${input.value}` }
      const interruptibleMw = netLoad(flexibility)
      const annualCost = input.value === baselineInput.value
        ? response.economics.annual_cost_usd
        : mapQuantiles(quantile => {
          // Rental/flexibility do not change exposure. Use the actual received
          // value rather than introducing an inverse/rescale rounding round trip.
          const exposure = site === request.site_exposure
            ? response.modeled_exposure[quantile]
            : exposureAtOne.modeled_exposure[quantile] * site
          return exposure * interruptibleMw * density * rental
            - exposure * vppOffset * assumptions.vpp_arbitrage_revenue_usd_per_mwh.value
        })
      const result = snapshot(annualCost, rental, flexibility, endpointSource)
      return {
        input: { ...input }, snapshot: result, source: endpointSource,
        delta_value_usd: sourced(result.net_value_usd.p50.value - baseline.net_value_usd.p50.value,
          endpointSource, 'delta_p50_comparison_value_usd'),
      }
    }
    const low = endpoint(bounds.low)
    const high = endpoint(bounds.high)
    return {
      key, label, unit, baseline_input: { ...baselineInput }, status: 'modeled', low, high,
      swing_usd: sourced(Math.abs(high.delta_value_usd.value - low.delta_value_usd.value), itemSource, 'swing_usd'),
      source: itemSource,
    }
  }
  const unavailableRow = (
    key: 'electricity_price' | 'utilization', label: string, unit: SensitivityRow['unit'],
    baselineInput: SourcedValue, bounds: { low: SourcedValue; high: SourcedValue }, reason: string,
  ): SensitivityRow => {
    const itemSource = rowSource(key, bounds.low, bounds.high)
    return {
      key, label, unit, baseline_input: { ...baselineInput }, status: 'not_modeled', reason,
      low: { input: { ...bounds.low }, delta_value_usd: null, snapshot: null, source: itemSource },
      high: { input: { ...bounds.high }, delta_value_usd: null, snapshot: null, source: itemSource },
      swing_usd: null, source: itemSource,
    }
  }
  const rentalSource = baseRental === assumptions.gpu_rental_price_usd_per_hour.value
    ? assumptions.gpu_rental_price_usd_per_hour
    : assumption(baseRental, `user://estimate/inputs/gpu_hour_value_usd; range source=${assumptions.gpu_rental_price_usd_per_hour.ref}`)
  const modeled = [
    modeledRow('gpu_rental_price', 'GPU rental price', 'USD/GPU-hour', rentalSource, range('gpu_rental_price_usd_per_hour')),
    modeledRow('flexibility_split', 'Flexibility split', 'fraction', assumption(request.flexibility_split, 'user://estimate/inputs/flexibility_split'), fractionRange),
    modeledRow('site_exposure', 'Site exposure factor', 'fraction', assumption(request.site_exposure, 'user://estimate/inputs/site_exposure'), fractionRange),
  ].sort((left, right) => (right.swing_usd!.value - left.swing_usd!.value) || left.key.localeCompare(right.key))
  return {
    baseline,
    rows: [...modeled,
      unavailableRow('electricity_price', 'Electricity price', 'USD/MWh', assumptions.industrial_electricity_price_usd_per_mwh,
        range('industrial_electricity_price_usd_per_mwh'), 'Electricity is informational in the current formula; no decision sensitivity is computed.'),
      unavailableRow('utilization', 'Utilization', 'fraction', defaultUtilizationRange.baseline,
        defaultUtilizationRange, 'Utilization is only in the documented revenue derivation; the current formula does not vary it.'),
    ],
    source,
    notes: [
      'Each modeled bar varies one input while holding the early-value assumption and all other inputs fixed.',
      'Bars show change in the p50 contract comparison value. Zero means the current scenario, not the decision threshold.',
      'Decisions use both p50 and p90 annual-cost paths over the term; these paths are comparison proxies, not quantiles of total contract loss.',
      'Documented price and utilization ranges are planning scenarios; flexibility and site exposure span their full user-set range.',
      'Electricity and utilization are not modeled, which does not mean they have zero economic effect.',
    ],
  }
}
