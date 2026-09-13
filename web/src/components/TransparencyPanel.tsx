import { useEffect } from 'react'
import { ExternalLink, X } from 'lucide-react'
import {
  CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { EstimateResponse, Source, SourcedValue } from '../model'
import { mockTransparencyDiagnostics } from '../model/transparency'
import { ConfidenceBadge, type ConfidenceEstimate } from './ConfidenceBadge'
import { SourceInfo, Sourced, SourcedTick } from './Sourced'
import { isMockSource } from './MockLabel'

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
    <div><span>Mean predicted probability</span><DiagnosticValue datum={row.point.mean_predicted} label="Assumed bin mean predicted probability" /></div>
    <div><span>Observed frequency</span><DiagnosticValue datum={row.point.observed_fraction} label="Assumed bin observed frequency" /></div>
    <span>Assumed grid-stress event fractions; no evaluation supplied.</span>
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
        <p id="transparency-diagnostics-notice" className="transparency-diagnostics-status">{isMockSource(diagnostics.brier_score) && <span>Evaluation unavailable.</span>} No held-out evaluation has been performed for these values. The scores and curve are assumed values, independent of the exposure slider.</p>
        <p>The reliability curve compares predicted grid-stress probability with observed grid-stress frequency. The Brier score measures squared probability error; lower is better. Neither diagnostic measures whether this site would be curtailed.</p>
        <div className="transparency-metrics">
          <div className="transparency-metric"><span>Assumed Brier score</span><DiagnosticValue datum={diagnostics.brier_score} label="Assumed Brier score, not computed" /><small>Evaluation unavailable</small></div>
          <div className="transparency-metric"><span>Assumed naive baseline</span><DiagnosticValue datum={diagnostics.naive_brier_score} label="Assumed naive Brier score, not computed" /><small>Assumed value, not a measured benchmark</small></div>
        </div>
        <div className="transparency-legend" aria-label="Reliability curve legend"><span className="transparency-legend-observed">Assumed reliability curve</span><span className="transparency-legend-reference">Perfect calibration reference</span></div>
        <div className="transparency-chart" role="group" aria-label="Assumed reliability curve for system stress; fractions, not measured probabilities">
          <span className="transparency-axis-label">Observed grid-stress frequency (fraction)</span>
          <ResponsiveContainer width="100%" height={225} minWidth={0}>
            <ComposedChart data={reliabilityRows} margin={{ top: 12, right: 20, bottom: 6, left: 8 }} accessibilityLayer>
              <CartesianGrid stroke="var(--border)" />
              <XAxis type="number" dataKey="predicted" domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tickLine={false} axisLine={{ stroke: 'var(--border)' }} tick={<SourcedTick source={axisSource} axis="x" />} />
              <YAxis type="number" domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} width={45} tickLine={false} axisLine={false} tick={<SourcedTick source={axisSource} axis="y" />} />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--text-muted)" strokeDasharray="4 4" />
              <Line type="linear" dataKey="observed" name="Assumed reliability curve" stroke="var(--chart-primary)" strokeWidth={2} dot={{ r: 3, fill: 'var(--chart-primary)' }} activeDot={{ r: 4, fill: 'var(--signal)', stroke: 'var(--signal)' }} isAnimationActive={false} />
              <Tooltip content={({ active, payload }) => <ReliabilityTooltip active={active} row={payload?.[0]?.payload as ReliabilityRow | undefined} />} wrapperStyle={{ pointerEvents: 'auto', zIndex: 30 }} cursor={{ stroke: 'var(--text-muted)', strokeDasharray: '3 4' }} />
            </ComposedChart>
          </ResponsiveContainer>
          <span className="transparency-axis-label">Mean predicted grid-stress probability (fraction)</span>
        </div>
        <table className="transparency-table"><caption>Assumed reliability bins · fractions</caption><thead><tr><th scope="col">Mean predicted probability</th><th scope="col">Observed frequency</th></tr></thead><tbody>{diagnostics.reliability_curve.map((point, index) => <tr key={point.mean_predicted.ref}><td><DiagnosticValue datum={point.mean_predicted} label={`Assumed bin ${index + 1} mean predicted probability`} /></td><td><DiagnosticValue datum={point.observed_fraction} label={`Assumed bin ${index + 1} observed frequency`} /></td></tr>)}</tbody></table>
      </section>

      <section className="transparency-section" aria-labelledby="transparency-tariff-title">
        <h4 id="transparency-tariff-title">Tariff evidence</h4>
        <p>{tariff.operator} · {tariff.curtailment_triggers.some(trigger => isMockSource(trigger.source)) || /\b(?:mock|illustrative)\b/i.test(tariff.service) ? 'Service terms not verified' : tariff.service}</p>
        <p>Contract language defines the operator's rights. Public system data does not establish whether a particular site would have been interrupted under those rights.</p>
        {tariff.curtailment_triggers.length === 0 ? <p className="transparency-empty">No extracted clauses have been supplied. No verified citation is available.</p> : <ul className="transparency-clauses">{tariff.curtailment_triggers.map((trigger, index) => {
          const mocked = isMockSource(trigger.source)
          const url = clauseUrl(trigger.source)
          return <li key={`${trigger.source.ref}-${index}`} className="transparency-clause">
            {mocked ? <span>Tariff evidence not supplied</span> : <strong>Supplied clause record</strong>}
            <p>{mocked ? 'System reliability may affect service; no extracted contract language has been supplied.' : trigger.text}</p>
            <div className="transparency-citation"><SourceInfo value={trigger.text} source={trigger.source} label="Tariff clause provenance" />{url ? <a href={url} target="_blank" rel="noopener noreferrer">Open supplied clause citation<ExternalLink size={12} aria-hidden="true" /></a> : <span>{mocked ? 'No verified citation available.' : 'Supplied reference: '}{!mocked && trigger.source.ref}</span>}</div>
            <small>{trigger.observable ? 'Marked observable in the supplied record; site applicability still requires review.' : 'Not established from the public system data available here.'}</small>
          </li>
        })}</ul>}
      </section>
    </div>
  </section>
}
