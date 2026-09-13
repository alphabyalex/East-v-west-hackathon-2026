import { useEffect } from 'react'
import { ExternalLink, X } from 'lucide-react'
import {
  CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { EstimateResponse, Source, SourcedValue } from '../model'
import { mockTransparencyDiagnostics } from '../model/transparency'
import { ConfidenceBadge, type ConfidenceEstimate } from './ConfidenceBadge'
import { SourceInfo, Sourced, SourcedTick } from './Sourced'
import { MockLabel, isMockSource } from './MockLabel'

export interface TransparencyPanelProps {
  confidence: ConfidenceEstimate
  tariff: EstimateResponse['tariff']
  siteExposure: SourcedValue
  onClose: () => void
}

const fraction = (value: number) => value.toLocaleString('en-US', { maximumFractionDigits: 2 })
const axisSource: Source = {
  source_type: 'assumption',
  ref: 'ui://transparency/probability-axis; fraction scale, not an observation',
}
const diagnostics = mockTransparencyDiagnostics
const reliabilityRows = diagnostics.reliability_curve.map(point => ({
  predicted: point.mean_predicted.value,
  observed: point.observed_fraction.value,
  point,
}))
type ReliabilityRow = typeof reliabilityRows[number]

function DiagnosticValue({ datum, label }: { datum: SourcedValue; label: string }) {
  return <Sourced value={datum.value} source={datum} label={label} format={fraction} />
}

function ReliabilityTooltip({ active, row }: { active?: boolean; row?: ReliabilityRow }) {
  if (!active || !row) return null
  return <div className="transparency-chart-tooltip">
    <span className="mock-label">Mock bin · not measured</span>
    <div><span>Mean predicted probability</span><DiagnosticValue datum={row.point.mean_predicted} label="Mock bin mean predicted probability" /></div>
    <div><span>Observed frequency</span><DiagnosticValue datum={row.point.observed_fraction} label="Mock bin observed frequency" /></div>
    <span>Fractions of grid-stress events; authored placeholders.</span>
  </div>
}

/** Only a supplied clause reference with an HTTP(S) URL becomes an external link. */
function clauseUrl(source: Source): string | null {
  if (source.source_type !== 'clause') return null
  try {
    const url = new URL(source.ref)
    return (url.protocol === 'https:' || url.protocol === 'http:') && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

interface HistoricalPrecedent {
  id: string
  title: string
  date: string
  scope: string
  duration: string
  drivers: string
  implication: string
  source: Source
  sourceUrl: string
}

const historicalPrecedents: HistoricalPrecedent[] = [
  {
    id: 'spp_20240826_eea1',
    title: 'SPP System-wide EEA1 Alert',
    date: 'August 26, 2024 · 12:30–15:00 CDT',
    scope: 'System Stress Event',
    duration: '150 minutes',
    drivers: 'High summer heat wave, low wind generator output, and unexpected resource forced outages.',
    implication: 'Demonstrates counterfactual system-stress window. Under CHILLS rules, system-wide EEA1 alerts represent a heightened state of watch but do not guarantee site-level power interruption.',
    source: {
      source_type: 'data',
      ref: 'https://spp.org/documents/72631/20241101_2024%20summer%20quarterly%20report_08-136-u.pdf#page=65',
    },
    sourceUrl: 'https://spp.org/documents/72631/20241101_2024%20summer%20quarterly%20report_08-136-u.pdf',
  },
  {
    id: 'spp_2024_conservative_ops',
    title: 'System Conservative Operations',
    date: 'July 16, Aug 1, 2, 26 & 27, 2024',
    scope: 'Operational Advisory',
    duration: '5 days active',
    drivers: 'Sustained regional heat waves causing load peaks and generation reserve margins to tighten.',
    implication: 'Advisory and watch dates only. System conservative operations alone are insufficient to trigger curtailment under the CHILLS contract rules.',
    source: {
      source_type: 'data',
      ref: 'https://spp.org/newsroom/stakeholder-report/',
    },
    sourceUrl: 'https://spp.org/newsroom/stakeholder-report/',
  },
  {
    id: 'shreveport_20250426_local_shed',
    title: 'SWEPCO Shreveport Local Load Shed',
    date: 'April 26, 2025 · 15:12–19:11 CDT',
    scope: 'Local Reliability / Voltage Emergency',
    duration: '239 minute instruction window',
    drivers: 'Warmer-than-forecast temperatures (90°F, 3–5°F above forecast), local load peaks, and contingency-related transformer loading concerns.',
    implication: 'Actual local curtailment evidence. Highlight of "local reliability" triggers: a system-wide status can remain green while localized transmission limitations mandate local load-shedding.',
    source: {
      source_type: 'data',
      ref: 'https://spp.org/documents/74283/spp\'s%20summary%20of%20the%20april%2026,%202025,%20shreveport-area%20load%20shed%20event.pdf#page=13',
    },
    sourceUrl: 'https://spp.org/documents/74283/spp\'s%20summary%20of%20the%20april%2026,%202025,%20shreveport-area%20load%20shed%20event.pdf',
  },
]

export function TransparencyPanel({ confidence, tariff, siteExposure, onClose }: TransparencyPanelProps) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      // The first Escape belongs to a currently inspected source, not the panel.
      if (document.querySelector('.provenance-popover')) return
      onClose()
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [onClose])

  return <section id="transparency-panel" className="transparency-panel" aria-labelledby="transparency-title">
    <div className="transparency-header">
      <div><span className="eyebrow muted">MODEL & EVIDENCE</span><h3 id="transparency-title">What supports this estimate</h3></div>
      <button type="button" className="text-button transparency-close" onClick={onClose} aria-label="Close model and evidence"><X size={15} aria-hidden="true" />Close</button>
    </div>
    <div className="transparency-body">
      <section className="transparency-section" aria-labelledby="transparency-honesty-title">
        <h4 id="transparency-honesty-title">System stress is not a site outage</h4>
        <p>Public grid data can establish when the system was under stress. It cannot establish whether a specific site would have been cut off: that depends on local transmission headroom we do not have.</p>
        <div className="transparency-assumption"><span>Your site exposure factor</span><DiagnosticValue datum={siteExposure} label="Site exposure factor assumption" /></div>
        <p>The site exposure factor is your assumption about how system stress maps to this site. It is not a fitted coefficient or a measured site risk. Adjust it with the visible slider to see what would change the decision.</p>
        <p>SPP&apos;s CHILLS tariff also names a &ldquo;local reliability issue&rdquo; as an independent curtailment trigger, separate from system-wide emergency conditions (FERC order 195 FERC &para;61,196 at &para;31). A purely local event at one interconnection point could trigger curtailment with no system-wide stress signal at all. This factor can only scale the modeled system-wide figure up or down; it cannot represent that local-only risk.</p>
        <ConfidenceBadge confidence={confidence} />
        <p>Confidence describes support for the estimate, not the probability that the future will match it. The model's ensemble agreement and historical precedent can support an estimate without establishing a site's actual interruptions.</p>
      </section>

      <section className="transparency-section" aria-labelledby="transparency-diagnostics-title" aria-describedby="transparency-diagnostics-notice">
        <h4 id="transparency-diagnostics-title">Model reliability</h4>
        <p id="transparency-diagnostics-notice" className="transparency-diagnostics-status"><MockLabel sources={[diagnostics.brier_score]} children="Mock diagnostics · not computed." /> No held-out evaluation has been performed for these values. The scores and curve are authored placeholders, independent of the exposure slider.</p>
        <p>The reliability curve compares predicted grid-stress probability with observed grid-stress frequency. The Brier score measures squared probability error; lower is better. Neither diagnostic measures whether this site would be curtailed.</p>
        <div className="transparency-metrics">
          <div className="transparency-metric"><span>Mock Brier score</span><DiagnosticValue datum={diagnostics.brier_score} label="Mock Brier score, not computed" /><small>Authored placeholder</small></div>
          <div className="transparency-metric"><span>Mock naive baseline</span><DiagnosticValue datum={diagnostics.naive_brier_score} label="Mock naive Brier score, not computed" /><small>Authored placeholder, not a measured benchmark</small></div>
        </div>
        <div className="transparency-legend" aria-label="Reliability curve legend"><span className="transparency-legend-observed">Mock reliability curve</span><span className="transparency-legend-reference">Perfect calibration reference</span></div>
        <div className="transparency-chart" role="group" aria-label="Mock reliability curve for system stress; fractions, not measured probabilities">
          <span className="transparency-axis-label">Observed grid-stress frequency (fraction)</span>
          <ResponsiveContainer width="100%" height={225} minWidth={0}>
            <ComposedChart data={reliabilityRows} margin={{ top: 12, right: 20, bottom: 6, left: 8 }} accessibilityLayer>
              <CartesianGrid stroke="var(--border)" />
              <XAxis type="number" dataKey="predicted" domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tickLine={false} axisLine={{ stroke: 'var(--border)' }} tick={<SourcedTick source={axisSource} axis="x" />} />
              <YAxis type="number" domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} width={45} tickLine={false} axisLine={false} tick={<SourcedTick source={axisSource} axis="y" />} />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--text-muted)" strokeDasharray="4 4" />
              <Line type="linear" dataKey="observed" name="Mock reliability curve" stroke="var(--teal)" strokeWidth={2} dot={{ r: 3, fill: 'var(--teal)' }} activeDot={{ r: 4 }} isAnimationActive={false} />
              <Tooltip content={({ active, payload }) => <ReliabilityTooltip active={active} row={payload?.[0]?.payload as ReliabilityRow | undefined} />} wrapperStyle={{ pointerEvents: 'auto', zIndex: 30 }} cursor={{ stroke: 'var(--text-muted)', strokeDasharray: '3 4' }} />
            </ComposedChart>
          </ResponsiveContainer>
          <span className="transparency-axis-label">Mean predicted grid-stress probability (fraction)</span>
        </div>
        <table className="transparency-table"><caption>Authored mock reliability bins · fractions</caption><thead><tr><th scope="col">Mean predicted probability</th><th scope="col">Observed frequency</th></tr></thead><tbody>{diagnostics.reliability_curve.map((point, index) => <tr key={point.mean_predicted.ref}><td><DiagnosticValue datum={point.mean_predicted} label={`Mock bin ${index + 1} mean predicted probability`} /></td><td><DiagnosticValue datum={point.observed_fraction} label={`Mock bin ${index + 1} observed frequency`} /></td></tr>)}</tbody></table>
      </section>

      <section className="transparency-section" aria-labelledby="transparency-precedents-title">
        <h4 id="transparency-precedents-title">Historical grid-stress precedents</h4>
        <p>Publicly documented operational events provide empirical counterfactuals for SPP&apos;s CHILLS curtailment triggers. These actual events illustrate the critical distinction between a system-wide alert and a local transmission constraint.</p>
        <ul className="precedents-list">
          {historicalPrecedents.map((precedent) => (
            <li key={precedent.id} className="precedent-card">
              <div className="precedent-card-header">
                <span className="precedent-card-title">
                  <strong>{precedent.title}</strong>
                </span>
                <span className={`precedent-card-badge ${precedent.scope.toLowerCase().includes('local') ? 'local' : ''}`}>
                  {precedent.scope}
                </span>
              </div>
              <div className="precedent-card-body">
                <dl className="precedent-card-meta">
                  <dt>DATE / TIME</dt>
                  <dd>{precedent.date}</dd>
                  <dt>DURATION</dt>
                  <dd>{precedent.duration}</dd>
                  <dt>DRIVERS</dt>
                  <dd>{precedent.drivers}</dd>
                </dl>
                <div className="precedent-card-implication">
                  <strong>CHILLS Implication:</strong> {precedent.implication}
                </div>
                <div className="transparency-citation">
                  <SourceInfo
                    value={precedent.title}
                    source={precedent.source}
                    label={`${precedent.title} provenance`}
                    displayText={precedent.title}
                  />
                  <a href={precedent.sourceUrl} target="_blank" rel="noopener noreferrer">
                    Open official report citation
                    <ExternalLink size={12} aria-hidden="true" />
                  </a>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="transparency-section" aria-labelledby="transparency-tariff-title">
        <h4 id="transparency-tariff-title">Tariff evidence</h4>
        <p>{tariff.operator} · {tariff.service}</p>
        <p>Contract language defines the operator's rights. Public system data does not establish whether a particular site would have been interrupted under those rights.</p>
        {tariff.curtailment_triggers.length === 0 ? <p className="transparency-empty">No extracted clauses have been supplied. No verified citation is available.</p> : <ul className="transparency-clauses">{tariff.curtailment_triggers.map((trigger, index) => {
          const mocked = isMockSource(trigger.source)
          const url = clauseUrl(trigger.source)
          return <li key={`${trigger.source.ref}-${index}`} className="transparency-clause">
            {mocked ? <MockLabel sources={[trigger.source]} children="Mock clause · not extracted" /> : <strong>Supplied clause record</strong>}
            <p>{trigger.text}</p>
            <div className="transparency-citation"><SourceInfo value={trigger.text} source={trigger.source} label="Tariff clause provenance" />{url ? <a href={url} target="_blank" rel="noopener noreferrer">Open supplied clause citation<ExternalLink size={12} aria-hidden="true" /></a> : <span>{mocked ? 'No verified citation available.' : 'Supplied reference: '}{!mocked && trigger.source.ref}</span>}</div>
            <small>{trigger.observable ? 'Marked observable in the supplied record; site applicability still requires review.' : 'Not established from the public system data available here.'}</small>
          </li>
        })}</ul>}
      </section>
    </div>
  </section>
}
