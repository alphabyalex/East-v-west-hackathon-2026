import { useEffect, useRef, useState } from 'react'
import { PortfolioError, portfolioRequest, type Portfolio } from '../api/portfolio'
import { PortfolioComparison } from './PortfolioComparison'
import { readPortfolioSession, writePortfolioSession } from '../api/portfolioSession'
import './portfolio.css'

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
    <div className="panel-heading"><h2 id="portfolio-title">Site portfolio</h2><span className="eyebrow muted">COMPARE SITES</span></div>
    <div className="portfolio-intro">
      <p>Compare saved sites. Add a site with Save to Portfolio in Scenario Stress Test.</p>
      {(portfolio || sessionId) && !expired && <button className="button button-quiet" disabled={busy} onClick={() => void run(signal => portfolioRequest(`/${encodeURIComponent(portfolio?.id ?? sessionId!)}`, 'GET', undefined, signal))}>Refresh evidence</button>}
      {error && <p role="alert">{error} {portfolio && 'Previously displayed comparison has not been refreshed.'}</p>}
      {expired && <button className="button" disabled={busy} onClick={() => void run(signal => portfolioRequest('', 'POST', undefined, signal))}>Start new portfolio session</button>}
      {!sites.length && <p role="status">No saved sites in this session.</p>}
    </div>
    {!!sites.length && <PortfolioComparison portfolio={portfolio!} disabled={busy || expired} onRemove={site => void run(signal => portfolioRequest(`/${portfolio!.id}/sites/${site.id}`, 'DELETE', undefined, signal))} />}
  </section>
}
