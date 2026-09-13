import { describe, expect, it } from 'vitest'
import { getGridImpact, powerScenario, validateGridImpact } from './grid-impact'
import { realGridFixture } from './grid-impact-fixtures'

describe('grid-impact transport and scenario', () => {
  it.each([['LES', 38200, 397026.85], ['OKGE', 15250, 103089.76]] as const)('derives %s from real precompiled prices without a normal-price assumption', (zone, mwh, usd) => {
    const data = realGridFixture(zone)
    if (data.cheap_power.status !== 'observed_hours_only') throw new Error('Missing committed evidence')
    const result = powerScenario(data.cheap_power, 100, .5)
    expect(result.wind.value).toBe(mwh)
    expect(result.dollars.value).toBeCloseTo(usd, 6)
    expect(result.dollars.source_type).toBe('assumption')
    expect(result.dollars.ref).toContain('not measured savings')
    expect(result.bins.reduce((sum, bin) => sum + bin.dollars.value, 0)).toBeCloseTo(usd, 6)
    expect(powerScenario(data.cheap_power, 200, .5).dollars.value).toBeCloseTo(usd * 2, 6)
    expect(powerScenario(data.cheap_power, 100, 0).dollars.value).toBe(0)
  })
  it('encodes the selection, forwards abort, and rejects the wrong zone', async () => {
    const signal = new AbortController().signal
    let url = '', init: RequestInit | undefined
    const result = await getGridImpact('LES', { signal, fetchImpl: async (input, options) => {
      url = String(input); init = options
      return new Response(JSON.stringify(realGridFixture()))
    } })
    expect(url).toMatch(/\/api\/grid-impact\/LES$/)
    expect(init?.signal).toBe(signal)
    expect(init?.cache).toBe('no-store')
    expect(result.location_id).toBe('LES')
    expect(() => validateGridImpact(result, 'OKGE')).toThrow(/identity/)
  })
  it.each(['NaN', 'missing source', 'wrong point', 'duplicate bin', 'wrong total', 'nonzero empty', 'negative', 'wrong reference'])('rejects %s without repairing numbers', damage => {
    const data = realGridFixture()
    if (data.cheap_power.status !== 'observed_hours_only') throw new Error('missing evidence')
    const power = data.cheap_power
    if (damage === 'NaN') power.usd_per_available_mw.value = NaN
    if (damage === 'missing source') power.proxy_hours.ref = ''
    if (damage === 'wrong point') power.source_location_id = 'SPPNORTH_HUB'
    if (damage === 'duplicate bin') power.bins[1].hour_utc.value = 0
    if (damage === 'wrong total') power.proxy_hours.value += 1
    if (damage === 'nonzero empty') power.bins.find(bin => bin.proxy_hours.value === 0)!.usd_per_available_mw.value = 10
    if (damage === 'negative') power.bins[0].usd_per_available_mw.value = -1
    if (damage === 'wrong reference') power.reference_price_usd_mwh.value = 82.1
    expect(() => validateGridImpact(data, 'LES')).toThrow()
  })
  it('keeps unavailable price and carbon as unavailable', () => {
    const data = realGridFixture('EDE')
    expect(data.cheap_power.status).toBe('unavailable')
    expect(data.carbon_shifted_tonnes_in_observed_hours.value).toBeNull()
  })
})
