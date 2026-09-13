import snapshot from '../model/economics-assumptions.json'

const dummyRankings = {
  operator: "SPP",
  composite_weight_formula: "0.5*S_risk + 0.3*S_wind + 0.2*S_carbon",
  description: "Composite sustainability and grid compatibility score per SPP balancing authority zone.",
  rankings: []
}

/** Tests route companion metadata independently so deferred POST timing stays explicit. */
export function withEconomics(post: typeof fetch, assumptions: unknown = snapshot): typeof fetch {
  return (url, options) => {
    const urlStr = String(url)
    if (urlStr.endsWith('/api/economics-assumptions')) {
      return Promise.resolve(new Response(JSON.stringify(assumptions), { status: 200 }))
    }
    if (urlStr.endsWith('/api/zone-rankings')) {
      return Promise.resolve(new Response(JSON.stringify(dummyRankings), { status: 200 }))
    }
    return post(url, options)
  }
}
