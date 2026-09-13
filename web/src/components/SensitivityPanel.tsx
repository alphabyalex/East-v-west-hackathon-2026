import { useEffect, useRef, useState } from 'react'
import { ChartNoAxesCombined } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { SensitivityEndpoint, SensitivityResult, SensitivityRow } from '../model/sensitivity'
import type { Source, SourcedValue } from '../model'
import { useChangeMotion } from '../hooks/useChangeMotion'
import { MockLabel } from './MockLabel'
import { Sourced, SourcedTick } from './Sourced'
import './SensitivityPanel.css'

type ModeledRow = Extract<SensitivityRow, { status: 'modeled' }>
type ChartRow = { label: string; span: [number, number]; original: ModeledRow }
const dollars = (value: number) => `${value < 0 ? '−' : ''}$${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 2 })}`
const compactDollars = (value: number) => `${value < 0 ? '−' : value > 0 ? '+' : ''}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`
const decisionText = (value: string) => value.replaceAll('_', ' ')
const percentileSource: Source = { source_type: 'assumption', ref: 'docs/ASSUMPTIONS.md#sensitivity-policy; percentile labels identify annual cost paths used for comparison, not percentiles of contract value' }

function SourceCopy({ text }: { text: string }) {
  return <>{text.split(/(\bp50\b|\bp90\b|\bp99\b)/).map((part, index) => /^p\d+$/.test(part)
    ? <Sourced key={index} value={Number(part.slice(1))} source={percentileSource} animate={false}>{part}</Sourced>
    : part)}</>
}

function Datum({ datum, label, format = dollars }: { datum: SourcedValue; label: string; format?: (value: number) => string }) {
  return <Sourced value={datum.value} source={datum} label={label} format={format} />
}

function InputValue({ row, datum, label }: { row: SensitivityRow; datum: SourcedValue; label: string }) {
  const format = row.unit === 'fraction'
    ? (value: number) => `${(value * 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}%`
    : dollars
  return <Datum datum={datum} label={label} format={format} />
}

function Decision({ endpoint, label }: { endpoint: SensitivityEndpoint; label: string }) {
  return <Sourced value={endpoint.snapshot.decision} source={endpoint.snapshot.source} label={label} className={`sensitivity-decision sensitivity-${endpoint.snapshot.decision}`}>{decisionText(endpoint.snapshot.decision)}</Sourced>
}

function TornadoBar({ x = 0, y = 0, width = 0, height = 0, payload }: { x?: number; y?: number; width?: number; height?: number; payload?: ChartRow }) {
  if (!payload) return null
  return <g className="sensitivity-bar" data-sensitivity-bar={payload.original.key}>
    <title>{JSON.stringify({ low: payload.original.low, high: payload.original.high, source: payload.original.source })}</title>
    {width === 0
      ? <line x1={x} x2={x} y1={y} y2={y + height} stroke="var(--chart-primary)" strokeWidth={2} />
      : <rect x={x} y={y} width={width} height={height} fill="var(--chart-primary)" fillOpacity={0.22} stroke="var(--chart-secondary)" />}
  </g>
}

function SensitivityTooltip({ active, row }: { active?: boolean; row?: ChartRow }) {
  if (!active || !row) return null
  return <div className="sensitivity-tooltip">
    <strong>{row.label}</strong>
    {(['low', 'high'] as const).map(bound => {
      const endpoint = row.original[bound]
      return <div key={bound} className="sensitivity-tooltip-endpoint">
        <span>{bound === 'low' ? 'Low input' : 'High input'} · <InputValue row={row.original} datum={endpoint.input} label={`${row.label} ${bound} input`} /></span>
        <span>Change · <Datum datum={endpoint.delta_value_usd} label={`${row.label} ${bound} change`} format={compactDollars} /></span>
        <Decision endpoint={endpoint} label={`${row.label} ${bound} decision`} />
      </div>
    })}
    <small><MockLabel sources={[row.original.source]} children="Mock sensitivity" /> Exact values and sources are available below.</small>
  </div>
}

function EndpointRow({ row, bound }: { row: ModeledRow; bound: 'low' | 'high' }) {
  const endpoint = row[bound]
  const label = `${row.label} ${bound}`
  return <tr>
    <th scope="row">{row.label}<small>{bound === 'low' ? 'Low input' : 'High input'} · {row.unit === 'fraction' ? 'share' : row.unit}</small></th>
    <td><InputValue row={row} datum={endpoint.input} label={`${label} input`} /></td>
    <td><Datum datum={endpoint.delta_value_usd} label={`${label} change`} /></td>
    <td><Datum datum={endpoint.snapshot.net_value_usd.p50} label={`${label} contract comparison`} /></td>
    <td><Decision endpoint={endpoint} label={`${label} decision`} /></td>
    <td>{endpoint.snapshot.breakeven_hours.value === null
      ? <Sourced value="No finite crossover" source={endpoint.snapshot.breakeven_hours} label={`${label} break-even`} description={endpoint.snapshot.breakeven_note} />
      : <Datum datum={endpoint.snapshot.breakeven_hours as SourcedValue} label={`${label} break-even hours per year`} format={value => value.toLocaleString('en-US', { maximumFractionDigits: 2 })} />}</td>
  </tr>
}

export function SensitivityPanel({ sensitivity }: { sensitivity: SensitivityResult }) {
  const modeled = sensitivity.rows.filter((row): row is ModeledRow => row.status === 'modeled')
  const unavailable = sensitivity.rows.filter(row => row.status === 'not_modeled')
  const chartRows: ChartRow[] = modeled.map(row => ({
    label: row.label,
    span: [Math.min(row.low.delta_value_usd.value, row.high.delta_value_usd.value) / 1_000_000,
      Math.max(row.low.delta_value_usd.value, row.high.delta_value_usd.value) / 1_000_000],
    original: row,
  }))
  // A symmetric scale makes the current-scenario reference legible even when both
  // endpoints lie on the same side (a custom input can be outside the chosen range).
  const maximum = Math.max(...chartRows.flatMap(row => row.span.map(Math.abs)))
  const axisLimit = maximum > 0 ? Math.ceil(maximum * 1.1 * 100) / 100 : 1
  const axisSource: Source = { ...sensitivity.source, ref: `${sensitivity.source.ref}; display axis in USD millions; tick * 1000000 = delta USD; chart scale, not an observation` }
  const [reducedMotion, setReducedMotion] = useState(true)
  const chartRef = useRef<HTMLDivElement>(null)
  useChangeMotion(chartRef, JSON.stringify(chartRows.map(row => [row.original.key, ...row.span])))
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReducedMotion(query.matches)
    sync()
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])
  return <section className="panel sensitivity-panel" aria-labelledby="sensitivity-title">
    <div className="panel-heading">
      <div className="flex items-center gap-2"><ChartNoAxesCombined size={15} className="muted" /><h2 id="sensitivity-title">Break-even sensitivity</h2></div>
      <span className="eyebrow muted">ONE ASSUMPTION AT A TIME</span>
    </div>
    <div className="sensitivity-body">
      <div className="sensitivity-intro">
        <p>Which assumptions move the decision? Bars span the low and high input scenarios, ordered by the largest dollar swing. Earlier-access contribution stays fixed.</p>
        <div className="sensitivity-baseline"><span>Current contract comparison · <Sourced value={50} source={percentileSource} animate={false}>p50</Sourced> path</span><strong><Datum datum={sensitivity.baseline.net_value_usd.p50} label="Current sensitivity contract comparison" format={compactDollars} /></strong><span><Sourced value={sensitivity.baseline.decision} source={sensitivity.baseline.source} label="Current sensitivity decision" className={`sensitivity-decision sensitivity-${sensitivity.baseline.decision}`}>{decisionText(sensitivity.baseline.decision)}</Sourced> <MockLabel sources={[sensitivity.source]} children="Mock sensitivity" /></span></div>
      </div>
      <div className="sensitivity-axis-caption">CHANGE IN CONTRACT COMPARISON · USD MILLIONS</div>
      <div ref={chartRef} className="sensitivity-chart" role="group" aria-label="Break-even sensitivity tornado chart, largest swing first. Inspect ranges and decision endpoints below for exact sourced values.">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart data={chartRows} layout="vertical" margin={{ top: 12, right: 35, bottom: 14, left: 4 }} accessibilityLayer>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis type="number" domain={[-axisLimit, axisLimit]} ticks={[-axisLimit, -axisLimit / 2, 0, axisLimit / 2, axisLimit]} tickLine={false} axisLine={{ stroke: 'var(--border-strong)' }} height={30} tick={<SourcedTick source={axisSource} axis="x" />} />
            <YAxis type="category" dataKey="label" width={136} axisLine={false} tickLine={false} tick={{ fill: 'var(--text-secondary)', fontSize: 11, fontFamily: 'var(--font-heading)', fontWeight: 600, letterSpacing: '-0.03em' }} />
            <ReferenceLine x={0} stroke="var(--text-primary)" strokeDasharray="3 4" />
            <Tooltip content={({ active, payload }) => <SensitivityTooltip active={active} row={payload?.[0]?.payload as ChartRow | undefined} />} cursor={{ fill: 'var(--hover)', fillOpacity: 0.5 }} wrapperStyle={{ pointerEvents: 'auto', zIndex: 30 }} />
            <Bar dataKey="span" shape={<TornadoBar />} barSize={24} isAnimationActive={!reducedMotion} animationDuration={320} animationEasing="ease-out" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="sensitivity-axis-note"><Sourced value={0} source={axisSource} animate={false} /> = current scenario. Positive increases value relative to the current scenario; negative decreases it. The reference line is not the decision threshold. Displayed amounts are rounded; provenance retains exact values.</p>
      <div className="sensitivity-ranges">
        {modeled.map(row => <div key={row.key} data-sensitivity-range={row.key}>
          <span>{row.label}<small>Current <InputValue row={row} datum={row.baseline_input} label={`${row.label} current input`} /> · {row.unit === 'fraction' ? 'share' : row.unit}</small><small className="sensitivity-range-decisions">Low input: <Decision endpoint={row.low} label={`${row.label} low decision`} /> · High input: <Decision endpoint={row.high} label={`${row.label} high decision`} /></small></span>
          <span><InputValue row={row} datum={row.low.input} label={`${row.label} range low`} /><span className="muted"> to </span><InputValue row={row} datum={row.high.input} label={`${row.label} range high`} /></span>
          <span><Datum datum={row.swing_usd} label={`${row.label} full swing`} format={value => compactDollars(value).replace(/^\+/, '')} /><small>full swing</small></span>
        </div>)}
      </div>
      <details className="sensitivity-details">
        <summary>Inspect ranges &amp; decision endpoints</summary>
        <div className="sensitivity-table-scroll" tabIndex={0} aria-label="Scrollable sensitivity endpoint table">
          <table className="sensitivity-table"><caption>Endpoint comparisons (rounded) · USD; break-even modeled exposure in hours/year. Exact values in provenance.</caption><thead><tr><th>Assumption</th><th>Input</th><th>Change vs. current</th><th>Contract comparison</th><th>Decision</th><th>Break-even · h/yr</th></tr></thead><tbody>{modeled.flatMap(row => [<EndpointRow key={`${row.key}-low`} row={row} bound="low" />, <EndpointRow key={`${row.key}-high`} row={row} bound="high" />])}</tbody></table>
        </div>
      </details>
      <div className="sensitivity-unmodeled" aria-label="Assumptions not modeled in the current formula">
        {unavailable.map(row => <div key={row.key} data-sensitivity-unmodeled={row.key}><div><strong>{row.label}</strong><span className="sensitivity-not-modeled">Not modeled</span></div><p>{row.reason}</p><p>Documented range: <InputValue row={row} datum={row.low.input} label={`${row.label} documented low`} /> to <InputValue row={row} datum={row.high.input} label={`${row.label} documented high`} /> {row.unit === 'fraction' ? 'share' : row.unit} · reference <InputValue row={row} datum={row.baseline_input} label={`${row.label} documented reference`} />.</p></div>)}
      </div>
      <div className="sensitivity-method">{sensitivity.notes.filter(note => !note.startsWith('Each modeled bar') && !note.startsWith('Bars show change')).map(note => <p key={note}><SourceCopy text={note} /></p>)}</div>
    </div>
  </section>
}
