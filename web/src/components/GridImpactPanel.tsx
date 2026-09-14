import { Component, lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useScenario } from '../ScenarioContext'
import { getGridImpact, powerScenario, type GridImpact, type GridDatum, type PowerEvidence } from '../api/grid-impact'
import { Sourced, SourceInfo, SourcedTick } from './Sourced'
import type { Source, SourcedValue } from '../model'
import './GridImpactPanel.css'

const Surface = lazy(() => import('./GridImpactSurface'))
class SurfaceBoundary extends Component<{ children: ReactNode; onUnavailable: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch() { this.props.onUnavailable() }
  render() { return this.state.failed ? null : this.props.children }
}
const dollars = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const monthName = (value: number) => new Date(Date.UTC(2025, value - 1)).toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
const availabilitySource: Source = { source_type: 'assumption', ref: 'user://grid-impact/upward_available_fraction; assumed share of selected load able to consume in qualifying hours; independent of interruption flexibility and site exposure; initial 0.5 matches published wind scenario' }

function Metric({ label, datum, unit, format, note }: { label: string; datum: GridDatum; unit: string; format?: (value: number) => string; note: string }) {
  return <div className="grid-impact-metric"><h3>{label}</h3><strong><Sourced label={label} value={datum.value ?? 'Unavailable'} source={datum} format={format} description={note} /></strong>{datum.value !== null && <span className="grid-impact-unit">{unit}</span>}<p>{note}</p></div>
}

function PowerChart({ power, load, available }: { power: PowerEvidence; load: number; available: number }) {
  const scenario = useMemo(() => powerScenario(power, load, available), [power, load, available])
  const [view, setView] = useState<'monthly' | 'surface'>('surface')
  const [fallback, setFallback] = useState(false)
  const monthly = useMemo(() => Array.from({ length: 12 }, (_, index) => {
    const bins = scenario.bins.slice(index * 24, index * 24 + 24)
    const sum = (key: 'wind' | 'dollars' | 'proxy_hours' | 'unknown_hours'): SourcedValue => ({
      value: bins.reduce((total, bin) => total + bin[key].value, 0), source_type: 'assumption',
      ref: `${bins[0][key].ref}; sum of supplied UTC-hour bins for month=${index + 1}`,
    })
    return { month: index + 1, usd: sum('dollars').value, dollars: sum('dollars'), wind: sum('wind'), hours: sum('proxy_hours'), unknown: sum('unknown_hours'), source: bins[0].month }
  }), [scenario])
  const failed = () => { setView('monthly'); setFallback(true) }
  return <>
    <div className="grid-impact-chart-heading"><h3>When the opportunity appears</h3><div className="chart-view-controls segment-control" role="group" aria-label="Grid-impact chart view">
      <button aria-pressed={view === 'monthly'} onClick={() => setView('monthly')}>Monthly</button>
      <button aria-pressed={view === 'surface'} onClick={() => { setFallback(false); setView('surface') }}>Surface</button>
    </div></div>
    {fallback && <p role="status">Surface unavailable on this device. Monthly chart and sourced values remain available.</p>}
    {view === 'surface' ? <SurfaceBoundary onUnavailable={failed}><Suspense fallback={<p role="status">Preparing grid-impact surface…</p>}><Surface bins={scenario.bins} onUnavailable={failed} /></Suspense></SurfaceBoundary> : <div className="grid-impact-chart" role="img" aria-label="Observed wholesale scenario value by month, in US dollars">
      <ResponsiveContainer width="100%" height={230}><BarChart data={monthly} margin={{ left: 24, right: 20, top: 12, bottom: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="month" tick={({ x, y, payload }) => <SourcedTick x={Number(x)} y={Number(y)} payload={{ value: monthName(Number(payload.value)) }} source={power.proxy_hours} />} />
        <YAxis tick={({ x, y, payload }) => <SourcedTick x={Number(x)} y={Number(y)} payload={payload} source={{ source_type: 'assumption', ref: 'display://grid-impact/USD; axis scale, not an observation' }} prefix="$" />} />
        <Tooltip content={({ active, payload }) => {
          const row = payload?.[0]?.payload as typeof monthly[number] | undefined
          return active && row ? <div className="sensitivity-tooltip"><strong>{monthName(row.month)}</strong><p><Sourced value={row.dollars.value} source={row.dollars} format={dollars} /></p><p><Sourced value={row.hours.value} source={row.hours} /> qualifying hours</p><p><Sourced value={row.unknown.value} source={row.unknown} /> unknown hours excluded</p></div> : null
        }} />
        <Bar dataKey="usd" fill="var(--text-muted)" isAnimationActive={false} />
      </BarChart></ResponsiveContainer>
    </div>}
    <details className="grid-impact-details"><summary>Inspect monthly values and sources</summary><div className="grid-impact-table"><table aria-label="Sourced monthly grid-impact values"><thead><tr><th>Month · UTC</th><th>Qualifying hours</th><th>Unknown hours</th><th>Wind scenario · MWh</th><th>Wholesale value · USD</th></tr></thead><tbody>{monthly.map(row => <tr key={row.month}>
      <th><Sourced value={row.month} source={row.source} format={monthName} animate={false} /></th>{[row.hours, row.unknown, row.wind, row.dollars].map((datum, index) => <td key={index}><Sourced value={datum.value} source={datum} format={index === 3 ? dollars : undefined} /></td>)}
    </tr>)}</tbody></table></div></details>
  </>
}

function AvailableImpact({ data, load }: { data: GridImpact; load: number }) {
  const [available, setAvailable] = useState(.5)
  const power = data.cheap_power.status === 'observed_hours_only' ? data.cheap_power : null
  const scenario = useMemo(() => power ? powerScenario(power, load, available) : null, [power, load, available])
  const wind = scenario?.wind ?? data.wind_absorption_mwh_in_observed_hours
  const unknownDollar: GridDatum = { value: null, source_type: 'assumption', ref: 'No matched precompiled hourly price evidence; dollar scenario unavailable' }
  function downloadEvidence() {
    const blob = new Blob([JSON.stringify({ response: data, scenario, controls: { selected_load_mw: load, upward_available_fraction: { value: available, ...availabilitySource } } }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `fluxline-grid-impact-${data.location_id}.json`; anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
  return <>
    <p className="grid-impact-intro">High-wind, nonpositive-price hours can align clean-energy consumption with cheaper power. These observed settlement-point hours support an energy and wholesale-value scenario; actual wasted-wind recovery and site access are unobserved.</p>
    <p className="grid-impact-scope">Reference: {data.location_mapping?.source_location_id ?? data.location_id} · within-area point, not a zone total. Observed period: <Sourced value={data.coverage.wind.period_start_utc?.slice(0, 10) ?? 'Unknown'} source={data.wind_absorption_mwh_in_observed_hours} /> to <Sourced value={data.coverage.wind.period_end_exclusive_utc?.slice(0, 10) ?? 'Unknown'} source={data.wind_absorption_mwh_in_observed_hours} /> (exclusive).</p>
    {power && <div className="grid-impact-controls"><label htmlFor="upward-availability">Upward available capacity</label><input id="upward-availability" type="range" min="0" max="1" step="0.05" value={available} onChange={event => setAvailable(Number(event.target.value))} /><Sourced value={available} source={availabilitySource} format={value => `${Math.round(value * 100)}%`} /><span>of <Sourced value={load} source={{ source_type: 'assumption', ref: 'user://scenario/load_mw; selected load for grid-impact scenario' }} /> MW</span><small>Assumed ability to consume in qualifying hours, independent of interruption split and site exposure.</small></div>}
    <div className="grid-impact-metrics">
      <Metric label="Wind absorption scenario" datum={wind} unit="MWh" note={scenario ? 'Potential consumption in observed qualifying hours, conditional on available capacity. Not measured curtailed wind captured.' : 'Published capacity scenario; not rescaled to the selected load without matched hourly evidence.'} />
      <Metric label="Cheap-power value" datum={scenario?.dollars ?? unknownDollar} unit="USD" format={dollars} note="Scenario value versus a zero wholesale price. Not measured or guaranteed bill savings." />
      <Metric label="Carbon shifted" datum={data.carbon_shifted_tonnes_in_observed_hours} unit="tonnes CO₂" note="Requires supported risk and makeup hours with carbon intensities. No carbon benefit is filled in when those inputs are missing." />
    </div>
    <div className="grid-impact-carbon">Associated wind operating emissions: <Sourced value={data.carbon_absorbed_tonnes_in_observed_hours.value ?? 'Unavailable'} source={data.carbon_absorbed_tonnes_in_observed_hours} /> tonnes CO₂ in the published scenario. This compatibility metric is not carbon absorbed, removed, or avoided.</div>
    {power && <>
      <p className="grid-impact-scope"><Sourced value={power.proxy_hours.value} source={power.proxy_hours} /> qualifying hours; <Sourced value={power.unknown_hours.value} source={power.unknown_hours} /> unknown hours excluded. Observed totals only, with no annual or contract-term extrapolation.</p>
      <PowerChart power={power} load={load} available={available} />
    </>}
    <details className="grid-impact-details"><summary>Calculation and evidence</summary><p>{power?.basis ?? 'Matched price inputs are unavailable; no dollar value is calculated.'}</p><p>Availability assumes that the selected capacity can consume in each qualifying hour. The reference price is {power ? <Sourced value={power.reference_price_usd_mwh.value} source={power.reference_price_usd_mwh} /> : 'unavailable'} USD/MWh. A retail contract may not pass negative wholesale prices through to the load.</p><p>Value source controls retain the exact calculation and references. Download includes the complete upstream evidence graph for pointer-based wind/carbon references.</p><button className="button" onClick={downloadEvidence}>Download grid-impact evidence</button></details>
  </>
}

export function GridImpactPanel() {
  const { inputs } = useScenario()
  const location = inputs.location_id
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<{ location: string; data?: GridImpact; error?: string }>()
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setState({ location })
    const timer = setTimeout(() => { controller.abort(); if (active) setState({ location, error: 'Grid-impact request timed out.' }) }, 5000)
    getGridImpact(location, { signal: controller.signal }).then(data => {
      if (active && !controller.signal.aborted) setState({ location, data })
    }).catch(() => { if (active && !controller.signal.aborted) setState({ location, error: 'Grid-impact data could not be loaded.' }) }).finally(() => clearTimeout(timer))
    return () => { active = false; clearTimeout(timer); controller.abort() }
  }, [location, attempt])
  const current = state?.location === location ? state : undefined
  const data = current?.data
  return <section className="panel grid-impact-panel" aria-labelledby="grid-impact-title"><div className="panel-heading"><h2 id="grid-impact-title">Grid Impact</h2><span className="eyebrow muted">CLEAN ENERGY &amp; POWER VALUE</span></div>
    {!current || (!current.data && !current.error) ? <p role="status">Loading grid-impact evidence for {location}…</p>
      : current.error ? <p role="status">Sustainability and cost data not available for {location}. {current.error} <button className="button" onClick={() => setAttempt(value => value + 1)}>Retry grid impact</button></p>
      : data && data.coverage.wind.status !== 'unavailable' ? <AvailableImpact key={location} data={data} load={inputs.load_mw} />
      : <div className="grid-impact-unavailable"><h3>Sustainability and cost data not yet available for this zone</h3><p>No supported settlement-point wind/price evidence is available for {location}. Wind absorption, carbon impact and dollar value remain unavailable.</p><SourceInfo value="Unavailable" source={data?.wind_absorption_mwh_in_observed_hours ?? { source_type: 'assumption', ref: 'No supplied grid-impact evidence' }} /></div>}
  </section>
}
