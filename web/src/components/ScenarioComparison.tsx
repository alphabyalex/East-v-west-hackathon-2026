import { useScenario } from '../ScenarioContext'
import { Check, ClipboardList, Trash2, Upload, AlertCircle } from 'lucide-react'
import { useState } from 'react'
import { PortfolioPanel } from './PortfolioPanel'

const numeric = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })
const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const money = (value: number) => `${value < 0 ? '−' : ''}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`

export function ScenarioComparison() {
  const {
    inputs,
    result,
    savedScenarios,
    saveScenario,
    deleteScenario,
    loadScenario,
    clearAllScenarios,
    location,
  } = useScenario()

  const [customName, setCustomName] = useState('')
  const [justSaved, setJustSaved] = useState(false)

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault()
    saveScenario(customName.trim() || undefined)
    setCustomName('')
    setJustSaved(true)
    setTimeout(() => setJustSaved(false), 2000)
  }

  return (
    <><PortfolioPanel /><section className="panel comparison-panel" aria-labelledby="comparison-title">
      <div className="panel-heading">
        <div className="flex items-center gap-2">
          <ClipboardList size={15} className="text-teal" />
          <h2 id="comparison-title">Scenario Comparison Ledger</h2>
        </div>
        <span className="eyebrow muted">SIDE-BY-SIDE LEDGER</span>
      </div>

      <div className="comparison-form-wrap">
        <form onSubmit={handleSave} className="comparison-save-form">
          <div className="comparison-input-group">
            <label htmlFor="comparison-scenario-name" className="sr-only">Scenario label</label>
            <input
              id="comparison-scenario-name"
              type="text"
              placeholder="Enter custom scenario label (optional)..."
              value={customName}
              onChange={e => setCustomName(e.target.value)}
              maxLength={45}
            />
            <button type="submit" className="button button-save-scen" aria-live="polite" disabled={location.enabled && !location.result}>
              {justSaved ? <Check size={13} className="text-mint" /> : <span aria-hidden="true">➕</span>}
              {justSaved ? 'Saved to Comparison' : 'Save Active Scenario'}
            </button>
          </div>
          {savedScenarios.length > 0 && (
            <button
              type="button"
              className="button button-quiet button-clear-all"
              onClick={clearAllScenarios}
            >
              Clear Comparison Ledger
            </button>
          )}
        </form>
      </div>

      {savedScenarios.length === 0 ? (
        <div className="comparison-empty" role="status">
          <AlertCircle size={18} className="muted" />
          <div>
            <p><strong>Comparison ledger is currently empty.</strong></p>
            <p>Adjust parameters in the inputs panel above, set your site exposure factor slider, then click &ldquo;Save Active Scenario&rdquo; to stack configurations side-by-side.</p>
          </div>
        </div>
      ) : (
        <div className="comparison-table-wrap" tabIndex={0} aria-label="Scenario comparison grid">
          <table className="comparison-table">
            <thead>
              <tr>
                <th scope="col" className="row-header">Scenario Parameters</th>
                {savedScenarios.map(scen => (
                  <th scope="col" key={scen.id} className="scenario-col-header">
                    <div className="scenario-header-title">{scen.name}</div>
                    <div className="scenario-header-actions">
                      <button
                        type="button"
                        className="btn-scen-action load"
                        onClick={() => loadScenario(scen.id)}
                        title="Load inputs into active workspace editor"
                        aria-label={`Load inputs for ${scen.name}`}
                      >
                        <Upload size={11} /> Load
                      </button>
                      <button
                        type="button"
                        className="btn-scen-action delete"
                        onClick={() => deleteScenario(scen.id)}
                        title="Delete from comparison"
                        aria-label={`Delete ${scen.name} from comparison`}
                      >
                        <Trash2 size={11} /> Delete
                      </button>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {/* INPUTS ROW GROUP */}
              <tr className="section-divider-row">
                <th colSpan={savedScenarios.length + 1} scope="colgroup">WORKSPACE INPUTS</th>
              </tr>
              <tr>
                <th scope="row" className="row-header">Location</th>
                {savedScenarios.map(scen => {
                  const locId = scen.inputs.location_id
                  const label = scen.locationEstimate?.location.name ?? (locId === 'SPP_SYSTEM' ? 'SPP System' : locId.replace('spp-', '').replace('-demo', '').toUpperCase())
                  return <td key={scen.id}><strong>{label}</strong></td>
                })}
              </tr>
              <tr>
                <th scope="row" className="row-header">Load size</th>
                {savedScenarios.map(scen => <td key={scen.id}>{integer.format(scen.inputs.load_mw)} MW</td>)}
              </tr>
              <tr>
                <th scope="row" className="row-header">Contract term</th>
                {savedScenarios.map(scen => <td key={scen.id}>{scen.inputs.contract_years} years</td>)}
              </tr>
              <tr>
                <th scope="row" className="row-header">Flexibility split</th>
                {savedScenarios.map(scen => <td key={scen.id}>{integer.format(scen.inputs.flexibility_percent)}% interruptible</td>)}
              </tr>
              <tr>
                <th scope="row" className="row-header">Exposure factor</th>
                {savedScenarios.map(scen => <td key={scen.id}>{scen.inputs.site_exposure.toFixed(2)}x</td>)}
              </tr>

              {/* OUTCOMES ROW GROUP */}
              <tr className="section-divider-row">
                <th colSpan={savedScenarios.length + 1} scope="colgroup">MODELED EXPOSURE (ANNUAL AVERAGE)</th>
              </tr>
              <tr>
                <th scope="row" className="row-header">p50 scenario (median)</th>
                {savedScenarios.map(scen => <td key={scen.id}>{scen.locationEstimate ? 'Unavailable' : `${numeric.format(scen.result.annual_exposure.p50.value)} h/yr`}</td>)}
              </tr>
              <tr>
                <th scope="row" className="row-header">p90 scenario (upper-tail)</th>
                {savedScenarios.map(scen => <td key={scen.id}>{scen.locationEstimate ? 'Unavailable' : `${numeric.format(scen.result.annual_exposure.p90.value)} h/yr`}</td>)}
              </tr>
              <tr>
                <th scope="row" className="row-header">p99 scenario (extreme-tail)</th>
                {savedScenarios.map(scen => <td key={scen.id}>{scen.locationEstimate ? 'Unavailable' : `${numeric.format(scen.result.annual_exposure.p99.value)} h/yr`}</td>)}
              </tr>
              {savedScenarios.some(scen => scen.locationEstimate) && <tr><th scope="row" className="row-header">Expected location exposure</th>{savedScenarios.map(scen => <td key={scen.id}>{scen.locationEstimate ? `${numeric.format(scen.locationEstimate.exposure.annual_expected_hours)} h/yr` : 'Unavailable'}</td>)}</tr>}
              <tr>
                <th scope="row" className="row-header">Break-even exposure</th>
                {savedScenarios.map(scen => (
                  <td key={scen.id}>
                    {(scen.locationEstimate ? scen.locationEstimate.economics.break_even_hours : scen.result.economics.break_even_exposure_hours.value) === null ? (
                      <span className="muted">No cost</span>
                    ) : (
                      `${integer.format((scen.locationEstimate ? scen.locationEstimate.economics.break_even_hours : scen.result.economics.break_even_exposure_hours.value) as number)} h/yr`
                    )}
                  </td>
                ))}
              </tr>

              {/* FINANCIALS ROW GROUP */}
              <tr className="section-divider-row">
                <th colSpan={savedScenarios.length + 1} scope="colgroup">ECONOMIC TERM LEDGER</th>
              </tr>
              <tr>
                <th scope="row" className="row-header">Earlier-access gain</th>
                {savedScenarios.map(scen => (
                  <td key={scen.id} className="text-mint">
                    +{money(scen.locationEstimate ? scen.locationEstimate.economics.early_access_value_usd : scen.result.economics.early_access_value_usd.value)}
                  </td>
                ))}
              </tr>
              <tr>
                <th scope="row" className="row-header">Interruption loss (median / expected)</th>
                {savedScenarios.map(scen => (
                  <td key={scen.id} className="text-amber">
                    −{money(scen.locationEstimate ? scen.locationEstimate.economics.term_cost_usd : scen.result.economics.term_loss_usd.value)}
                  </td>
                ))}
              </tr>
              <tr className="ledger-total-row">
                <th scope="row" className="row-header">Net Scenario Value</th>
                {savedScenarios.map(scen => {
                  const val = scen.locationEstimate ? scen.locationEstimate.economics.net_value_usd : scen.result.economics.net_value_usd.value
                  const stateClass = val > 0 ? 'text-mint' : val < 0 ? 'text-negative' : 'muted'
                  return (
                    <td key={scen.id} className={stateClass}>
                      <strong>{val >= 0 ? '+' : ''}{money(val)}</strong>
                    </td>
                  )
                })}
              </tr>
              <tr className="decision-row">
                <th scope="row" className="row-header">Recommendation</th>
                {savedScenarios.map(scen => {
                  const decision = scen.locationEstimate ? 'Expected value only' : scen.result.decision
                  const stateClass = decision === 'worth it' ? 'pos' : decision === 'not worth it' ? 'neg' : 'neu'
                  return (
                    <td key={scen.id}>
                      <span className={`scen-decision-badge ${stateClass}`}>{decision.toUpperCase()}</span>
                    </td>
                  )
                })}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section></>
  )
}
