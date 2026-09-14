import { useEffect, useRef, useState, type FormEvent } from 'react'
import { BookmarkPlus } from 'lucide-react'
import { useScenario } from '../ScenarioContext'
import { toEstimateRequest } from '../model'
import { getLocations } from '../api/locations'
import { PortfolioError, portfolioRequest } from '../api/portfolio'
import { readPortfolioSession, writePortfolioSession } from '../api/portfolioSession'
import './save-to-portfolio.css'

export function SaveToPortfolio() {
  const { inputs, location } = useScenario()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const [expired, setExpired] = useState(false)
  const pending = useRef<AbortController | null>(null)
  const field = useRef<HTMLInputElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const blocked = location.enabled
    ? 'City estimates cannot be saved to the zone portfolio. Select a real SPP zone before saving; use Export scenario to keep a city result.'
    : inputs.location_id === 'SPP_SYSTEM' || inputs.location_id.startsWith('spp-')
      ? 'Select a real SPP zone before saving. The system aggregate and scenario locations are not individual zones.'
      : ''

  useEffect(() => () => pending.current?.abort(), [])
  useEffect(() => { if (open) field.current?.focus() }, [open])
  useEffect(() => { setSaved(''); setError('') }, [inputs, location.enabled])

  function close() {
    if (busy) return
    setOpen(false)
    trigger.current?.focus()
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (pending.current) return
    setSaved(''); setError('')
    if (blocked) { setError(blocked); return }
    const siteName = name.trim()
    if (!siteName) { setError('Enter a name to save this site.'); field.current?.focus(); return }
    if (expired) { setError('Start a new portfolio session before saving.'); return }
    // Capture the submitted inputs before any awaits; later edits cannot change
    // which scenario this request saves. Economics overrides aren't in this API.
    const submitted = { name: siteName, inputs: toEstimateRequest(inputs) }
    const controller = new AbortController(); pending.current = controller
    setBusy(true)
    try {
      const catalog = await getLocations({ signal: controller.signal })
      if (!catalog.some(item => item.id === submitted.inputs.location_id && item.kind === 'zone')) {
        throw new Error('Select a real SPP zone from the current location list before saving.')
      }
      let id = readPortfolioSession()
      if (!id) {
        const created = await portfolioRequest('', 'POST', undefined, controller.signal)
        id = created.id
        // Keep the session even if add_site fails, so retry doesn't abandon it.
        writePortfolioSession(id)
      }
      const result = await portfolioRequest(`/${encodeURIComponent(id)}/sites`, 'POST', submitted, controller.signal)
      writePortfolioSession(result.id)
      if (!controller.signal.aborted) {
        setSaved(`Saved ${siteName} to Portfolio & Alerts.`)
        setName('')
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof Error ? failure.message : 'Could not save this site. Try again.')
        if (failure instanceof PortfolioError && failure.status === 404 && /portfolio session/i.test(failure.message)) setExpired(true)
      }
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  async function restart() {
    if (pending.current) return
    const controller = new AbortController(); pending.current = controller; setBusy(true)
    try {
      const result = await portfolioRequest('', 'POST', undefined, controller.signal)
      writePortfolioSession(result.id)
      if (!controller.signal.aborted) { setExpired(false); setError('') }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not start a portfolio session.')
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return <div className="portfolio-save-control">
    <button ref={trigger} type="button" className="button" aria-expanded={open} aria-controls="portfolio-save-form"
      onClick={() => { if (open) close(); else { setOpen(true); setSaved(''); setError('') } }}>
      <BookmarkPlus size={14} />Save to Portfolio
    </button>
    {open && <form id="portfolio-save-form" className="portfolio-save-popover" aria-label="Save scenario to portfolio" onSubmit={save}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); close() } }}>
      <label htmlFor="portfolio-site-name">Site name</label>
      <input ref={field} id="portfolio-site-name" value={name} onChange={event => { setName(event.target.value); setError(''); setSaved('') }}
        maxLength={80} disabled={busy} aria-invalid={error.startsWith('Enter a name') || undefined} />
      {(blocked || error) && <p role="alert">{blocked || error}</p>}
      {saved && <p role="status" className="portfolio-save-success">{saved}</p>}
      {busy && <p role="status">Saving to portfolio…</p>}
      <div className="portfolio-save-actions">
        <button className="button" type="submit" disabled={busy}>Save site</button>
        <button className="button button-quiet" type="button" onClick={close} disabled={busy}>Close</button>
      </div>
      {expired && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void restart()}>Start new portfolio session</button>}
    </form>}
  </div>
}
