// Test-only readers of the committed real evidence; never imported by the app.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { validateGridImpact } from './grid-impact'

const folder = resolve(process.cwd(), '../data/processed/grid_impact/live_v1')
export function realGridFixture(zone = 'LES') {
  const point = zone === 'LES' ? 'LES_LES' : zone === 'OKGE' ? 'OKGE_OKGE' : zone
  const result = JSON.parse(readFileSync(resolve(folder, `${point}.snapshot.json`), 'utf8')).result
  const prices = JSON.parse(readFileSync(resolve(folder, 'cheap_power_v1.json'), 'utf8')).result.locations
  result.location_id = zone
  if (point !== zone) result.location_mapping = { source_location_id: point, scope: 'point_reference_scenario_not_zone_total_or_site_deliverability' }
  result.cheap_power = prices[point] ?? { status: 'unavailable', reason: 'No matched hourly price evidence', bins: [] }
  return validateGridImpact(result, zone)
}
