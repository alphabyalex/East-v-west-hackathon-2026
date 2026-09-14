import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import manifest from '../../../data/processed/national_stack/zone_rankings.json'
import { Sourced } from './Sourced'
import type { Source } from '../model'
import './overview.css'

const previewSource: Source = {
  source_type: 'assumption',
  ref: 'mock://overview/interaction; authored example only, not pipeline output; reference exposure=100 h/year; preview hours=100 * local exposure factor; no scenario inputs or API calls',
}
const evidenceSource: Source = {
  source_type: 'data',
  ref: 'data/processed/national_stack/zone_rankings.json#/available_wind_evidence; count of distinct location_id values with supplied wind-screening evidence; not composite ranks or site deliverability',
}
const evidenceCount = new Set(manifest.available_wind_evidence.map(row => row.location_id)).size
const referenceHours = 100

export function Overview({ onScenario, onZones }: { onScenario: () => void; onZones: () => void }) {
  // Deliberately independent of ScenarioContext: this demonstrates the mapping,
  // never supplies a site estimate or edits the viewer's working scenario.
  const [exposure, setExposure] = useState(0.4)
  const hours = referenceHours * exposure
  return <section className="overview" aria-labelledby="overview-title">
    <div className="overview-hero">
      <div className="overview-intro">
        <span className="eyebrow muted">FLEXIBLE GRID INTERCONNECTION</span>
        <h1 id="overview-title">Connect sooner.{' '}<br />Understand the trade-off.</h1>
        <p>Earlier power comes with a condition: your load can be interrupted. Fluxline helps you weigh that exposure against the cost of waiting for a firm connection.</p>
        <p>Explore a location, test your assumptions, and see what would change the decision before committing to the deal.</p>
        <div className="overview-actions">
          <button className="button" onClick={onScenario}>Open scenario stress test<ArrowRight size={14} /></button>
          <button className="text-button" onClick={onZones}>Explore SPP zone evidence<ArrowRight size={14} /></button>
        </div>
      </div>

      <section className="panel overview-preview" aria-labelledby="preview-title" aria-describedby="preview-description">
        <div className="panel-heading"><h2 id="preview-title">Move the assumption.</h2><span id="preview-description" className="eyebrow muted">INTERACTIVE PREVIEW</span></div>
        <div className="overview-preview-body">
          <div className="overview-preview-result">
            <div><span className="eyebrow muted">EXPOSURE</span><div className="overview-preview-number"><Sourced value={hours} source={previewSource} label="Exposure" format={v => v.toFixed(1)} /><small>h/year</small></div></div>
            <p>Reference<br /><Sourced value={referenceHours} source={previewSource} /> h/year<br />× exposure factor</p>
          </div>
          <div className="overview-preview-control">
            <div className="slider-readout"><label htmlFor="overview-exposure">Site exposure factor</label><Sourced value={exposure} source={previewSource} label="Preview factor" format={v => v.toFixed(2)} className="exposure-number" /></div>
            <div className="slider-track-wrap">
              <span className="slider-progress" style={{ width: `calc(10px + (100% - 20px) * ${exposure})` }} aria-hidden="true" />
              <input id="overview-exposure" type="range" min={0} max={1} step={0.01} value={exposure}
                onChange={event => setExposure(Number(event.target.value))} aria-label="Preview site exposure factor"
                aria-describedby="preview-description" aria-valuetext={`${exposure.toFixed(2)}, interactive preview`}
                data-provenance={JSON.stringify({ value: exposure, ...previewSource })} />
            </div>
            <div className="slider-endpoints"><span><Sourced value={0} source={previewSource} format={v => v.toFixed(1)} animate={false} /> No exposure</span><span>Full exposure <Sourced value={1} source={previewSource} format={v => v.toFixed(1)} animate={false} /></span></div>
          </div>
        </div>
      </section>
    </div>

    <section className="overview-promise" aria-labelledby="overview-promise-title">
      <span className="eyebrow muted">THE HONESTY LAYER</span>
      <div><h2 id="overview-promise-title">Evidence where we have it.<br />Explicit limits where we don’t.</h2><p>We never fabricate a score — if evidence is insufficient, we say so. Public grid stress does not establish a specific site’s actual curtailment. You set the site exposure assumption; every displayed number carries its source.</p></div>
    </section>

    <section className="overview-method" aria-labelledby="overview-method-title">
      <div className="overview-section-heading"><span className="eyebrow muted">UNDER THE HOOD</span><h2 id="overview-method-title">From grid history to a decision you can inspect.</h2></div>
      <dl className="overview-method-rows">
        <div><dt>Model the exposure</dt><dd>Precomputed SPP models turn historical grid conditions into modeled exposure. The national research architecture stacks <Sourced value={6} source={{ source_type: 'assumption', ref: 'pipeline/national_stacking.py#REGIONS; six configured research regions using synthetic SPP proxies, not six independently observed grids or validated national coverage' }} /> regional models into a meta-learner; its synthetic regional proxies are research work, not validated national coverage.</dd></div>
        <div><dt>Inspect the distribution</dt><dd>Rotate the exposure surface and inspect supplied quantiles over the contract term. The view makes the spread visible; it does not turn model uncertainty into a site forecast. City comparisons that supply expected hours alone do not get invented tail quantiles.</dd></div>
        <div><dt>Connect energy and economics</dt><dd>Wind-oversupply screening, carbon accounting and cheap-power scenarios connect grid conditions with the value of flexible load. Missing observations or carbon factors stay unavailable; wind screening is not proof of recoverable energy at a site.</dd></div>
      </dl>
      <div className="overview-evidence"><div className="overview-evidence-count"><Sourced value={evidenceCount} source={evidenceSource} label="Zones with supplied wind-screening evidence" /><span>SPP zones with supplied<br />wind-screening evidence</span></div><p>Current shipped evidence, not a permanent ceiling. Coverage can grow as more observations are verified.{manifest.status === 'unavailable' && ' Composite rankings remain unavailable while annual exposure and avoided-carbon evidence are incomplete.'}</p><button className="text-button" onClick={onZones}>Inspect zone evidence<ArrowRight size={14} /></button></div>
    </section>

  </section>
}
