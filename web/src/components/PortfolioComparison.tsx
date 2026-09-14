import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import type { Portfolio, PortfolioSite } from '../api/portfolio'
import { getGridImpact, powerScenario, type GridImpact } from '../api/grid-impact'
import type { Source, SourcedValue } from '../model'
import { Sourced } from './Sourced'

type Row = { key: string; label: string; unit: string; values: (SourcedValue | null)[]; note?: string; bars?: boolean }
const number = (value: number) => value.toLocaleString('en-US', { maximumFractionDigits: 1 })
const usd = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const derived = (site: PortfolioSite, value: number, formula: string): SourcedValue => ({ value, source_type: 'assumption', ref: `${site.input_source.ref}; saved_site=${site.id}; ${formula}; scenario calculation from saved inputs` })
const availabilitySource: Source = { source_type: 'assumption', ref: 'user://portfolio/upward_available_fraction; common comparison control, not a saved scenario input; independent of interruption flexibility and site exposure' }

export function PortfolioComparison({ portfolio, disabled, onRemove }: { portfolio: Portfolio; disabled: boolean; onRemove: (site: PortfolioSite) => void }) {
  const { sites } = portfolio
  const [referenceId, setReferenceId] = useState(sites[0]?.id ?? '')
  const [differences, setDifferences] = useState(false)
  const [availability, setAvailability] = useState(.5)
  const [impact, setImpact] = useState<{ portfolio: Portfolio; data: Record<string, GridImpact | null> }>()
  const referenceIndex = Math.max(0, sites.findIndex(site => site.id === referenceId))
  const reference = sites[referenceIndex]
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const timeout = setTimeout(() => controller.abort(), 5000)
    const zones = [...new Set(sites.map(site => site.inputs.location_id))]
    void Promise.all(zones.map(async zone => {
      try { return [zone, await getGridImpact(zone, { signal: controller.signal })] as const }
      catch { return [zone, null] as const }
    })).then(entries => { if (active) setImpact({ portfolio, data: Object.fromEntries(entries) }) })
      .finally(() => clearTimeout(timeout))
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [portfolio, sites])
  const ready = impact?.portfolio === portfolio
  const grid = sites.map(site => ready ? impact.data[site.inputs.location_id] : null)
  const power = grid.map((data, i) => data?.cheap_power.status === 'observed_hours_only' ? powerScenario(data.cheap_power, sites[i].inputs.load_mw, availability) : null)
  const input = (key: keyof PortfolioSite['inputs'], scale = 1) => sites.map(site => derived(site, Number(site.inputs[key] ?? 0) * scale, `${key}=${site.inputs[key] ?? 0}; display multiplier=${scale}`))
  const sections: { label: string; note?: string; rows: Row[] }[] = [
    { label: 'Capacity & flexibility', rows: [
      { key: 'load', label: 'Requested load', unit: 'MW', values: input('load_mw'), bars: true },
      { key: 'interruptible', label: 'Interruptible load', unit: 'MW', values: sites.map(site => derived(site, site.inputs.load_mw * site.inputs.flexibility_split, `interruptible_mw=${site.inputs.load_mw}*${site.inputs.flexibility_split}; before VPP support`)), bars: true, note: 'Before VPP support' },
      { key: 'base', label: 'Non-interruptible share', unit: 'MW', values: sites.map(site => derived(site, site.inputs.load_mw * (1 - site.inputs.flexibility_split), `non_interruptible_mw=${site.inputs.load_mw}*(1-${site.inputs.flexibility_split}); does not establish firm service`)), bars: true },
      { key: 'split', label: 'Flexibility split', unit: '%', values: input('flexibility_split', 100) },
      { key: 'term', label: 'Contract term', unit: 'years', values: input('term_years') },
      { key: 'exposure', label: 'Site exposure factor', unit: '', values: input('site_exposure') },
      { key: 'homes', label: 'VPP support', unit: 'solar homes', values: input('vpp_solar_homes') },
    ] },
    { label: 'Clean energy & power value', note: 'Observed-period scenarios, not annual or contract totals', rows: [
      { key: 'wind', label: 'Observed wind-screening hours', unit: 'hours', values: sites.map((site, i) => site.wind_evidence?.proxy_hours ?? (grid[i]?.cheap_power.status === 'observed_hours_only' ? grid[i].cheap_power.proxy_hours : null)), bars: true },
      { key: 'mwh', label: 'Wind absorption scenario', unit: 'MWh', values: power.map(item => item?.wind ?? null), bars: true, note: 'Conditional on available capacity' },
      { key: 'value', label: 'Cheap-power value', unit: 'USD', values: power.map(item => item?.dollars ?? null), bars: true, note: 'Wholesale scenario versus zero price' },
      { key: 'per-mw', label: 'Value per available MW', unit: 'USD / MW', values: grid.map(data => data?.cheap_power.status === 'observed_hours_only' ? data.cheap_power.usd_per_available_mw : null), note: 'Compare opportunity independent of load size' },
      { key: 'carbon', label: 'Carbon shifted', unit: 'tonnes CO₂', values: grid.map(data => data?.carbon_shifted_tonnes_in_observed_hours.value != null ? data.carbon_shifted_tonnes_in_observed_hours as SourcedValue : null) },
    ] },
  ]
  if (sites.some(site => site.ranking.status === 'available')) sections.push({ label: 'Published ranking', rows: [
    { key: 'rank', label: 'Zone rank', unit: '', values: sites.map(site => site.ranking.status === 'available' ? { value: site.ranking.zone_rank!, ...site.ranking.source } : null) },
    { key: 'score', label: 'Composite score', unit: '', values: sites.map(site => site.ranking.status === 'available' ? { value: site.ranking.composite_score!, ...site.ranking.source } : null) },
  ] })
  const summarySource = useMemo<Source>(() => ({ source_type: 'assumption', ref: `user://portfolio/${portfolio.id}; summary of saved candidate scenarios, not an operating fleet; sites=${sites.map(site => site.id).join(',')}` }), [portfolio.id, sites])
  const windowFor = (i: number, key: string) => {
    const wind = sites[i].wind_evidence
    const coverage = grid[i]?.coverage.wind
    const start = key === 'wind' && wind ? wind.period_start_utc : coverage?.period_start_utc
    const end = key === 'wind' && wind ? wind.period_end_exclusive_utc : coverage?.period_end_exclusive_utc
    const from = Date.parse(start ?? '')
    const to = Date.parse(end ?? '')
    return Number.isFinite(from) && Number.isFinite(to) && from < to ? `${from}/${to}` : null
  }
  const same = (row: Row) => row.values.every(value => value?.value === row.values[0]?.value)
  const visible = sections.map(section => ({ ...section, rows: section.rows.filter(row => !differences || !same(row)) }))
  return <>
    <div className="portfolio-overview" aria-label="Comparison summary">
      <div><span>Saved candidates</span><strong><Sourced value={sites.length} source={summarySource} format={number} /></strong></div>
      <div><span>Zones represented</span><strong><Sourced value={new Set(sites.map(site => site.inputs.location_id)).size} source={summarySource} format={number} /></strong></div>
      <div><span>Requested load range</span><strong><Sourced value={Math.min(...sites.map(site => site.inputs.load_mw))} source={summarySource} format={number} /><span className="portfolio-range-separator"> to </span><Sourced value={Math.max(...sites.map(site => site.inputs.load_mw))} source={summarySource} format={number} /><small>MW</small></strong></div>
    </div>
    <div className="portfolio-comparison-controls">
      <label>Compare against<select aria-label="Comparison reference site" value={reference.id} onChange={event => setReferenceId(event.target.value)}>{sites.map(site => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
      <button className="button" aria-pressed={differences} onClick={() => setDifferences(value => !value)}>Differences only</button>
      <label className="portfolio-availability">Available capacity<input aria-label="Comparison upward available capacity" type="range" min="0" max="1" step=".05" value={availability} onChange={event => setAvailability(Number(event.target.value))} /><Sourced value={availability} source={availabilitySource} format={value => `${Math.round(value * 100)}%`} /></label>
    </div>
    <div className="portfolio-scroll" role="region" aria-label="Saved site comparison" tabIndex={0}><table className="portfolio-table" style={{ '--site-count': sites.length } as CSSProperties}>
      <thead><tr><th scope="col"><span className="eyebrow">SIDE BY SIDE</span><span className="portfolio-table-title">Scenario comparison</span><small>Deltas against {reference.name}</small></th>{sites.map((site, i) => <th scope="col" key={site.id} data-reference={i === referenceIndex}>
        <span className="portfolio-site-name">{site.name}</span><span className="portfolio-location">{site.inputs.location_id}</span>
        <div className="portfolio-site-load"><Sourced value={site.inputs.load_mw} source={site.input_source} format={number} /><small>MW</small></div>
        <button className="button button-quiet" disabled={disabled} onClick={() => onRemove(site)}>Remove {site.name}</button>
      </th>)}</tr></thead>
      {visible.filter(section => section.rows.length).map(section => <tbody key={section.label}>
        <tr className="portfolio-section"><th colSpan={sites.length + 1} scope="colgroup">{section.label}{section.note && <small>{section.note}</small>}</th></tr>
        {section.rows.map(row => {
          const maximum = Math.max(0, ...row.values.map(value => value?.value ?? 0))
          const baseline = row.values[referenceIndex]
          return <tr key={row.key} data-comparison-row={row.key}><th scope="row">{row.label}<small>{row.unit}</small>{row.note && <span className="portfolio-row-note">{row.note}</span>}</th>{sites.map((site, i) => {
            const datum = row.values[i]
            const comparable = section.label !== 'Clean energy & power value' || (windowFor(i, row.key) !== null && windowFor(i, row.key) === windowFor(referenceIndex, row.key))
            const delta = datum && baseline && comparable ? datum.value - baseline.value : null
            const format = row.unit.startsWith('USD') ? usd : number
            return <td key={site.id}>{datum ? <>
              <Sourced value={datum.value} source={datum} format={format} description={row.note} />
              {row.bars && <div className="portfolio-meter" aria-hidden="true"><span style={{ width: `${maximum ? datum.value / maximum * 100 : 0}%` }} /></div>}
              {i !== referenceIndex && datum && baseline && !comparable && <div className="portfolio-delta">Periods differ</div>}
              {i !== referenceIndex && delta !== null && <div className="portfolio-delta">{delta === 0 ? 'Same as reference' : <><Sourced value={delta} source={{ source_type: 'assumption', ref: `comparison delta=${datum.value}-${baseline!.value}; current=[${datum.ref}]; reference_site=${reference.id}; reference=[${baseline!.ref}]; difference only, not a recommendation` }} format={value => `${value > 0 ? '+' : '-'}${format(Math.abs(value))}`} /> vs reference</>}</div>}
            </> : <span className="portfolio-unavailable">{!ready && ['mwh', 'value', 'per-mw', 'carbon'].includes(row.key) ? 'Loading...' : 'Unavailable'}</span>}</td>
          })}</tr>
        })}
      </tbody>)}
    </table></div>
    {differences && visible.every(section => !section.rows.length) && <p className="portfolio-empty-diff">No differences in the available measures.</p>}
  </>
}
