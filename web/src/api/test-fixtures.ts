import snapshot from '../model/economics-assumptions.json'
import { offlineLocations } from './locations'

/** Tests route companion metadata independently so deferred POST timing stays explicit. */
export function withEconomics(post: typeof fetch, assumptions: unknown = snapshot): typeof fetch {
  return (url, options) => String(url).endsWith('/api/economics-assumptions')
    ? Promise.resolve(new Response(JSON.stringify(assumptions), { status: 200 }))
    : String(url).endsWith('/api/locations')
      ? Promise.resolve(new Response(JSON.stringify({ locations: offlineLocations }), { status: 200 }))
      : post(url, options)
}
