import { apiUrl, type EstimateClientOptions } from './client'
import { mockResponse } from '../model/fixture'

export interface LocationOption {
  id: string
  label: string
  kind: 'system' | 'zone' | 'scenario'
}

/** Existing offline choices; real zone membership comes from the backend catalog. */
export const offlineLocations: LocationOption[] = mockResponse.locations.map(location => ({
  id: location.id,
  label: location.label.replace(/ · illustrative$/, ' · scenario'),
  kind: location.id === 'SPP_SYSTEM' ? 'system' : 'scenario',
}))

export async function getLocations(
  { signal, fetchImpl = globalThis.fetch }: EstimateClientOptions = {},
): Promise<LocationOption[]> {
  const response = await fetchImpl(apiUrl('/api/locations'), {
    headers: { Accept: 'application/json' }, signal,
  })
  if (!response.ok) throw new Error(`Location list unavailable (HTTP ${response.status}).`)
  const body: unknown = await response.json()
  if (!body || typeof body !== 'object' || !('locations' in body)
      || !Array.isArray(body.locations) || body.locations.length === 0) {
    throw new Error('Invalid location catalog.')
  }
  const seen = new Set<string>()
  return body.locations.map((entry: unknown) => {
    if (!entry || typeof entry !== 'object' || !('id' in entry) || !('label' in entry) || !('kind' in entry)
        || typeof entry.id !== 'string' || !entry.id.trim() || entry.id !== entry.id.trim()
        || typeof entry.label !== 'string' || !entry.label.trim()
        || typeof entry.kind !== 'string' || !['system', 'zone', 'scenario'].includes(entry.kind) || seen.has(entry.id)) {
      throw new Error('Invalid or duplicate location catalog entry.')
    }
    seen.add(entry.id)
    return { id: entry.id, label: entry.label, kind: entry.kind as LocationOption['kind'] }
  })
}
