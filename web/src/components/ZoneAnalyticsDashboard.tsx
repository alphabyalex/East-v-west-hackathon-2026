import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import shippedManifest from '../../../data/processed/national_stack/zone_rankings.json'
import './zone-analytics-dashboard.css'

type RawEvidenceRow = Record<string, unknown>

type EvidenceRow = {
  zone: string
  reference: string
  highWind: number
  evaluable: number
  unknown: number
  coverage: number
}

const manifest = shippedManifest as unknown as { available_wind_evidence?: RawEvidenceRow[] }
const rawRows = Array.isArray(manifest.available_wind_evidence) ? manifest.available_wind_evidence : []

function findNumber(row: RawEvidenceRow, patterns: RegExp[]) {
  const entry = Object.entries(row).find(([key, value]) => patterns.some(pattern => pattern.test(key)) && (typeof value === 'number' || (typeof value === 'object' && value !== null && 'value' in value)))
  if (typeof entry?.[1] === 'number') return Math.max(0, entry[1])
  if (typeof entry?.[1] === 'object' && entry[1] !== null && 'value' in entry[1] && typeof entry[1].value === 'number') {
    return Math.max(0, entry[1].value)
  }
  return 0
}

function findText(row: RawEvidenceRow, patterns: RegExp[]) {
  const entry = Object.entries(row).find(([key, value]) => patterns.some(pattern => pattern.test(key)) && typeof value === 'string')
  return entry ? String(entry[1]) : ''
}

const evidenceRows: EvidenceRow[] = rawRows.map(row => {
  const highWind = findNumber(row, [/high.*wind/i, /screened.*hour/i, /qualifying.*hour/i, /wind.*hour/i, /proxy.*hour/i])
  const evaluable = findNumber(row, [/evaluable.*hour/i, /observed.*hour/i])
  const unknown = findNumber(row, [/unknown.*hour/i])
  const denominator = evaluable + unknown
  return {
    zone: findText(row, [/^location_id$/i, /^zone_id$/i, /^zone$/i]) || 'Unknown',
    reference: findText(row, [/reference/i, /settlement/i, /point/i]),
    highWind,
    evaluable,
    unknown,
    coverage: denominator ? (evaluable / denominator) * 100 : 0,
  }
}).filter(row => row.zone !== 'Unknown')

const hours = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const sortedRows = [...evidenceRows].sort((a, b) => b.highWind - a.highWind)
const topSignal = sortedRows[0]
const medianEvaluable = [...evidenceRows].sort((a, b) => a.evaluable - b.evaluable)[Math.floor(evidenceRows.length / 2)]?.evaluable ?? 0
const medianUnknown = [...evidenceRows].sort((a, b) => a.unknown - b.unknown)[Math.floor(evidenceRows.length / 2)]?.unknown ?? 0

function Metric({ label, value, detail, accent = 'teal' }: { label: string; value: string; detail: string; accent?: 'teal' | 'amber' }) {
  return <article className={`zone-dashboard-metric zone-dashboard-metric-${accent}`}>
    <span className="zone-dashboard-metric-label">{label}</span>
    <strong>{value}</strong>
    <span className="zone-dashboard-metric-detail">{detail}</span>
  </article>
}

export function ZoneAnalyticsDashboard() {
  if (!evidenceRows.length) return null

  return <section className="zone-dashboard" aria-labelledby="zone-dashboard-title">
    <div className="zone-dashboard-header">
      <div>
        <span className="eyebrow">EVIDENCE DASHBOARD</span>
        <h2 id="zone-dashboard-title">Read the signal before ranking the zones.</h2>
        <p>Three views of the supplied wind-screening observations. Nothing here is a composite score, a site forecast, or an estimate of recoverable wind.</p>
      </div>
      <div className="zone-dashboard-status" aria-label="Dashboard data status">
        <span className="zone-dashboard-status-dot" aria-hidden="true" />
        <span>SHIPPED EVIDENCE</span>
        <small>2025 observation window</small>
      </div>
    </div>

    <div className="zone-dashboard-metrics">
      <Metric label="Zones with evidence" value={hours.format(evidenceRows.length)} detail="supplied wind-screening rows" />
      <Metric label="Highest observed signal" value={`${hours.format(topSignal?.highWind ?? 0)} h`} detail={`${topSignal?.zone ?? 'No zone'} screened hours`} accent="amber" />
      <Metric label="Evaluated period" value={`${hours.format(medianEvaluable)} h`} detail="median across supplied rows" />
      <Metric label="Unknown hours" value={`${hours.format(medianUnknown)} h`} detail="retained, never filled" accent="amber" />
    </div>

    <div className="zone-dashboard-charts">
      <article className="zone-dashboard-chart-card zone-dashboard-chart-card-wide">
        <div className="zone-dashboard-chart-heading">
          <div><span className="eyebrow">01 / OBSERVED SIGNAL</span><h3>Screened hours by zone</h3></div>
          <span className="zone-dashboard-chart-unit">HOURS</span>
        </div>
        <p>High-wind and nonpositive-price hours in each supplied settlement-point reference.</p>
        <div className="zone-dashboard-chart zone-dashboard-chart-large" aria-label="Horizontal bar chart of screened hours by zone">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={sortedRows} layout="vertical" margin={{ top: 4, right: 18, bottom: 4, left: 4 }}>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" horizontal={false} />
              <XAxis type="number" tick={{ fill: '#8d9495', fontSize: 11 }} axisLine={{ stroke: 'rgba(255,255,255,0.14)' }} tickLine={false} />
              <YAxis type="category" dataKey="zone" width={44} tick={{ fill: '#c8cecd', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ fill: 'rgba(116,215,207,0.06)' }} />
              <Bar dataKey="highWind" name="Screened hours" fill="#79d9d0" radius={[0, 2, 2, 0]} barSize={18} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </article>

      <article className="zone-dashboard-chart-card">
        <div className="zone-dashboard-chart-heading">
          <div><span className="eyebrow">02 / DATA QUALITY</span><h3>Observed window integrity</h3></div>
          <span className="zone-dashboard-chart-unit">HOURS</span>
        </div>
        <p>Evaluated and unknown hours remain distinct so missingness stays visible.</p>
        <div className="zone-dashboard-chart" aria-label="Stacked bar chart of evaluated and unknown hours by zone">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={sortedRows} margin={{ top: 4, right: 8, bottom: 4, left: -18 }}>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey="zone" tick={{ fill: '#8d9495', fontSize: 10 }} axisLine={{ stroke: 'rgba(255,255,255,0.14)' }} tickLine={false} />
              <YAxis tick={{ fill: '#8d9495', fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
              <Bar dataKey="evaluable" name="Evaluated" stackId="hours" fill="#79d9d0" radius={[2, 2, 0, 0]} />
              <Bar dataKey="unknown" name="Unknown" stackId="hours" fill="#d7a56d" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="zone-dashboard-legend"><span><i className="zone-dashboard-legend-teal" />Evaluated</span><span><i className="zone-dashboard-legend-amber" />Unknown</span></div>
      </article>

      <article className="zone-dashboard-chart-card">
        <div className="zone-dashboard-chart-heading">
          <div><span className="eyebrow">03 / COVERAGE</span><h3>Signal versus support</h3></div>
          <span className="zone-dashboard-chart-unit">PERCENT</span>
        </div>
        <p>Each point shows supplied signal against its observed evidence coverage.</p>
        <div className="zone-dashboard-chart" aria-label="Scatter chart of screened hours versus evidence coverage">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 12, bottom: 14, left: -12 }}>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" />
              <XAxis type="number" dataKey="highWind" name="Screened hours" tick={{ fill: '#8d9495', fontSize: 10 }} axisLine={{ stroke: 'rgba(255,255,255,0.14)' }} tickLine={false} />
              <YAxis type="number" dataKey="coverage" name="Coverage" domain={[0, 100]} tick={{ fill: '#8d9495', fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ strokeDasharray: '3 3' }} />
              <Scatter data={sortedRows} fill="#d7a56d" />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
        <div className="zone-dashboard-chart-note">Coverage = evaluated hours / (evaluated + unknown).</div>
      </article>
    </div>

    <p className="zone-dashboard-footnote">These references are within-zone settlement points, not zone totals. The existing evidence table below retains exact dates, provenance, and every exclusion. Composite rankings stay unavailable until the required annual exposure and avoided-carbon evidence exists.</p>
  </section>
}
