import { afterEach, describe, expect, it, vi } from 'vitest'
import { getLocations } from './locations'

afterEach(() => { vi.unstubAllEnvs() })

describe('location catalog transport boundary', () => {
  it('uses GET and the configured API base, forwarding cancellation and exact returned IDs', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8123/')
    const signal = new AbortController().signal
    const locations = [{ id: 'FUTURE_ZONE_42', label: 'Authored future catalog zone', kind: 'zone' }]
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ locations })))
    expect(await getLocations({ fetchImpl, signal })).toEqual(locations)
    const [url, options] = fetchImpl.mock.calls[0]
    expect(url).toBe('http://127.0.0.1:8123/api/locations')
    expect(options?.method ?? 'GET').toBe('GET')
    expect(options?.signal).toBe(signal)
    expect(options?.headers).toEqual({ Accept: 'application/json' })
  })

  it.each([
    null,
    { locations: [] },
    { locations: [{ id: ' CSWS', label: 'CSWS zone', kind: 'zone' }] },
    { locations: [{ id: 'CSWS', label: '', kind: 'zone' }] },
    { locations: [{ id: 'CSWS', label: 'CSWS zone', kind: 'invented-kind' }] },
    { locations: [{ id: 'CSWS', label: 'CSWS zone', kind: ['zone'] }] },
    { locations: [{ id: 'CSWS', label: 'One', kind: 'zone' }, { id: 'CSWS', label: 'Two', kind: 'zone' }] },
  ])('rejects a malformed or ambiguous catalog without repairing IDs', async body => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(body)))
    await expect(getLocations({ fetchImpl })).rejects.toThrow(/location catalog/i)
  })

  it('rejects an unavailable endpoint so the component can keep its existing choices', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', { status: 503 }))
    await expect(getLocations({ fetchImpl })).rejects.toThrow(/503/)
  })
})
