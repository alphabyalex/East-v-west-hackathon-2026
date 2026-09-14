import { useEffect, useRef, useState, type FormEvent } from 'react'
import { PortfolioError, portfolioRequest, type Portfolio, type PortfolioSite, type Thresholds } from '../api/portfolio'
import { Sourced } from './Sourced'
import { readPortfolioSession, writePortfolioSession } from '../api/portfolioSession'
import './portfolio.css'

const labels = { not_configured: 'No thresholds set', breached: 'Threshold exceeded', within_thresholds: 'Within saved thresholds', unavailable: 'Threshold check unavailable' }
const thresholdNames = { exposure_p90_hours_above: 'Modeled exposure p90 above (h/year)', confidence_score_below: 'Confidence score below' }

function ThresholdEditor({ site, disabled, onSave }: { site: PortfolioSite; disabled: boolean; onSave: (thresholds: Thresholds) => void }) {
  const [hours, setHours] = useState(site.thresholds.exposure_p90_hours_above?.toString() ?? '')
  const [confidence, setConfidence] = useState(site.thresholds.confidence_score_below?.toString() ?? '')
  useEffect(() => {
    setHours(site.thresholds.exposure_p90_hours_above?.toString() ?? '')
    setConfidence(site.thresholds.confidence_score_below?.toString() ?? '')
  }, [site.thresholds.exposure_p90_hours_above, site.thresholds.confidence_score_below])
  function submit(event: FormEvent) {
    event.preventDefault()
    onSave({ exposure_p90_hours_above: hours === '' ? null : Number(hours), confidence_score_below: confidence === '' ? null : Number(confidence) })
  }
  return <form onSubmit={submit} className="portfolio-thresholds">
    <label>{thresholdNames.exposure_p90_hours_above}<input aria-label={`Exposure threshold for ${site.name}`} type="number" min="0" step="any" value={hours} onChange={e => setHours(e.target.value)} disabled={disabled} /></label>
    <label>{thresholdNames.confidence_score_below}<input aria-label={`Confidence threshold for ${site.name}`} type="number" min="0" max="1" step="any" value={confidence} onChange={e => setConfidence(e.target.value)} disabled={disabled} /></label>
    <p className="muted">Leave blank to disable a check. Equality does not breach.</p>
    <button className="button button-quiet" disabled={disabled}>Save thresholds for {site.name}</button>
  </form>
}

export function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [expired, setExpired] = useState(false)
  const mounted = useRef(false)
  const request = useRef<AbortController | null>(null)

  function failure(error: unknown) {
    setError(error instanceof Error ? error.message : 'Portfolio unavailable. Retry when the API is available.')
    if (error instanceof PortfolioError && error.status === 404 && /portfolio session/i.test(error.message)) setExpired(true)
  }
  useEffect(() => {
    mounted.current = true
    const id = readPortfolioSession()
    if (id) {
      setSessionId(id)
      const controller = new AbortController(); request.current = controller; setBusy(true)
      portfolioRequest(`/${encodeURIComponent(id)}`, 'GET', undefined, controller.signal)
        .then(data => { if (!controller.signal.aborted) setPortfolio(data) })
        .catch(error => { if (!controller.signal.aborted) failure(error) })
        .finally(() => { if (!controller.signal.aborted) setBusy(false) })
    }
    return () => { mounted.current = false; request.current?.abort() }
  }, [])

  async function run(action: (signal: AbortSignal) => Promise<Portfolio>) {
    if (busy) return
    const controller = new AbortController(); request.current = controller
    setBusy(true); setError('')
    try {
      const data = await action(controller.signal)
      if (!mounted.current || controller.signal.aborted) return
      setPortfolio(data); setSessionId(data.id); setExpired(false)
      writePortfolioSession(data.id)
    } catch (error) { if (mounted.current && !controller.signal.aborted) failure(error) }
    finally { if (mounted.current && !controller.signal.aborted) setBusy(false) }
  }
  const sites = portfolio?.sites ?? []
  return <section className="panel portfolio-panel" aria-labelledby="portfolio-title" aria-busy={busy}>
    <div className="panel-heading"><h2 id="portfolio-title">Site portfolio</h2><span className="eyebrow muted">COMPARE / SET THRESHOLDS</span></div>
    <div className="portfolio-intro">
      <p>Compare saved sites and manage their thresholds. Add a site with Save to Portfolio in Scenario Stress Test.</p>
      <p className="muted">Session-only storage: saved sites expire after a day of inactivity or an API restart. Checks use precomputed evidence and refresh manually; no live monitoring or notifications. Portfolio economics use server defaults.</p>
      {(portfolio || sessionId) && !expired && <button className="button button-quiet" disabled={busy} onClick={() => void run(signal => portfolioRequest(`/${encodeURIComponent(portfolio?.id ?? sessionId!)}`, 'GET', undefined, signal))}>Refresh evidence and checks</button>}
      {error && <p role="alert">{error} {portfolio && 'Previously displayed comparison has not been refreshed.'}</p>}
      {expired && <button className="button" disabled={busy} onClick={() => void run(signal => portfolioRequest('', 'POST', undefined, signal))}>Start new portfolio session</button>}
      {!sites.length && <p role="status">No saved sites in this session.</p>}
      {portfolio && <p className="muted">Checked at <Sourced value={portfolio.checked_at_utc} source={{ source_type: 'data', ref: 'api://portfolios/request-time; evaluation time, not observation time' }} />. Refresh manually to read updated artifacts.</p>}
    </div>
    {!!sites.length && <div className="portfolio-scroll" role="region" aria-label="Saved site comparison" tabIndex={0}><table className="portfolio-table">
      <thead><tr><th scope="col">Comparison</th>{sites.map(site => <th scope="col" key={site.id}>{site.name}<span className="muted portfolio-location">{site.inputs.location_id}</span><button className="button button-quiet" disabled={busy || expired} onClick={() => void run(signal => portfolioRequest(`/${portfolio!.id}/sites/${site.id}`, 'DELETE', undefined, signal))}>Remove {site.name}</button></th>)}</tr></thead>
      <tbody>
        {([
          ['load_mw', 'Load (MW)'], ['term_years', 'Contract term (years)'], ['flexibility_split', 'Interruptible fraction'], ['site_exposure', 'Site exposure factor'], ['vpp_solar_homes', 'VPP solar homes'],
        ] as const).map(([key, label]) => <tr key={key}><th scope="row">{label}</th>{sites.map(site => <td key={site.id}><Sourced value={site.inputs[key] ?? 0} source={site.input_source} /></td>)}</tr>)}
        <tr><th scope="row">Published zone rank / composite score</th>{sites.map(site => <td key={site.id}>{site.ranking.status === 'available' ? <><Sourced value={site.ranking.zone_rank!} source={site.ranking.source} /> / <Sourced value={site.ranking.composite_score!} source={site.ranking.source} /></> : <><strong>Composite ranking unavailable</strong><details><summary>Missing evidence</summary><ul>{site.ranking.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul></details></>}</td>)}</tr>
        <tr><th scope="row">Observed wind-screening hours</th>{sites.map(site => <td key={site.id}>{site.wind_evidence ? <><Sourced value={site.wind_evidence.proxy_hours.value} source={site.wind_evidence.proxy_hours} /><p className="muted">Reference: {site.wind_evidence.reference_location_id}. Screened hours, not measured recoverable wind or site deliverability.</p><details><summary>Observation coverage</summary><p><Sourced value={site.wind_evidence.period_start_utc} source={site.wind_evidence.proxy_hours} /> to <Sourced value={site.wind_evidence.period_end_exclusive_utc} source={site.wind_evidence.proxy_hours} /> (end exclusive).</p><p><Sourced value={site.wind_evidence.evaluable_hours.value} source={site.wind_evidence.evaluable_hours} /> evaluable hours; <Sourced value={site.wind_evidence.unknown_hours.value} source={site.wind_evidence.unknown_hours} /> unknown.</p></details></> : 'Wind evidence unavailable for this zone.'}</td>)}</tr>
        <tr><th scope="row">Modeled exposure / annual cost</th>{sites.map(site => <td key={site.id}>{site.estimate ? <>{(['p50', 'p90', 'p99'] as const).map(q => <p key={q}>{q}: <Sourced value={site.estimate!.modeled_exposure[q]} source={site.estimate!.modeled_exposure.source} /> h/year · $<Sourced value={site.estimate!.economics.annual_cost_usd[q]} source={site.estimate!.economics.source} />/year</p>)}<p className="muted">Costs are scenario calculations under the current sourced economics assumptions.</p></> : <><strong>Exposure and cost unavailable</strong><details><summary>Why unavailable</summary><p>{site.estimate_unavailable_reason}</p></details></>}</td>)}</tr>
        <tr><th scope="row">Your static threshold checks</th>{sites.map(site => <td key={site.id}><strong className={site.threshold_status.status === 'breached' ? 'portfolio-breached' : ''}>{labels[site.threshold_status.status]}</strong>{site.threshold_status.checks.map(check => <div key={check.metric} className="portfolio-check"><p>{thresholdNames[check.metric]}: <Sourced value={check.threshold} source={check.threshold_source} /></p><p>{check.status === 'unavailable' ? 'Unavailable: no supported observation to check.' : <>{check.status === 'breached' ? 'Exceeded' : 'Within threshold'} · current <Sourced value={check.observed!} source={check.observed_source!} /></>}</p></div>)}<ThresholdEditor site={site} disabled={busy || expired} onSave={thresholds => void run(signal => portfolioRequest(`/${portfolio!.id}/sites/${site.id}/thresholds`, 'PUT', thresholds, signal))} /></td>)}</tr>
      </tbody>
    </table></div>}
  </section>
}
