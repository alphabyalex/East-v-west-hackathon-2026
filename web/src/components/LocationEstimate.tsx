import { Activity, ArrowDownRight, ArrowRight, CircleHelp } from 'lucide-react';
import { CartesianGrid, ComposedChart, Line, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { useScenario } from '../ScenarioContext';
import { Sourced, SourceInfo } from './Sourced';
import type { Source } from '../model';

const numeric = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const money = (value: number) => `${value < 0 ? '−' : ''}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`;

export function LocationSearchField() {
  const { location } = useScenario();
  return <form className="location-search" onSubmit={event => { event.preventDefault(); void location.submit(); }}>
    <label htmlFor="location-query" className="sr-only">City, state or coordinates</label>
    <input id="location-query" list="location-suggestions" value={location.query} maxLength={120} required
      placeholder="Wichita, KS or latitude, longitude" onChange={event => location.changeQuery(event.target.value)} />
    <datalist id="location-suggestions">{location.suggestions.map(query => <option key={query} value={query} />)}</datalist>
    {location.selection && location.selection.candidates.length > 1 && <>
      <label htmlFor="location-match" className="field-note">Choose the matching location</label>
      <select id="location-match" value={location.selection.index ?? ''} onChange={event => { if (event.target.value !== '') void location.submit(Number(event.target.value)); }}>
        <option value="">Select a match</option>
        {location.selection.candidates.map((point, index) => <option key={index} value={index}>{point.name} ({point.latitude}, {point.longitude})</option>)}
      </select>
    </>}
    <button className="button" disabled={location.busy} type="submit">{location.busy ? 'Estimating…' : 'Estimate location'}<ArrowRight size={12} /></button>
    <span className="field-note">Uses the facility and economic inputs on this page.</span>
  </form>;
}

export function LocationConnection() {
  const { location } = useScenario();
  const message = location.error || (location.phase === 'searching' ? 'Finding your location…'
    : location.phase === 'estimating' ? 'Calculating location exposure. New locations may take a few minutes.'
    : location.phase === 'choosing' ? 'Several locations matched. Choose the correct location.'
    : location.result ? `${location.result.location.name} · estimate matches the current inputs.`
    : location.stale ? 'Inputs changed. Select Estimate location to update the results.'
    : 'Choose a city and state or coordinates, then select Estimate location.');
  return <section className="estimate-connection" aria-label="Estimate data connection">
    <div className="estimate-connection-copy"><p role={location.error ? 'alert' : 'status'}>{message}</p></div>
  </section>;
}

function Metric({ value, source, unit, label, primary = false }: { value: number; source: Source; unit: string; label: string; primary?: boolean }) {
  return <div className={`metric${primary ? ' metric-primary' : ''}`}>
    <div className="metric-label"><span>{label}</span></div>
    <div className="metric-number"><Sourced value={value} source={source} format={numeric.format} /><span className="metric-unit">{unit}</span></div>
    <Sourced value="Low" source={source} label={`${label}: Low confidence`} className="confidence-badge" animate={false}>Confidence: Low</Sourced>
  </div>;
}

export function LocationResultPanels() {
  const { location, inputs } = useScenario();
  const result = location.result;
  if (!result) return <div className="results-grid" aria-busy={location.busy}>
    {['Modeled exposure', 'Connection economics'].map(title => <section className="panel location-pending" key={title}>
      <div className="panel-heading"><h2>{title}</h2></div>
      <div className="metric-number">—</div>
      <p>{location.busy ? 'Your estimate is processing.' : 'Estimate a location to see results for the current inputs.'}</p>
    </section>)}
  </div>;
  const { exposure: x, economics: e, source, assumption_source: mapped, economic_source: economic } = result;
  const sourced = (value: number, field: string, format = numeric.format) => <Sourced value={value} source={{ ...economic, ref: `${economic.ref}; ${field}` }} format={format} />;
  const years = Array.from({ length: inputs.contract_years }, (_, i) => ({ year: i + 1, hours: x.annual_expected_hours }));
  return <div className="results-grid">
    <section className="panel exposure-panel" aria-labelledby="location-exposure-title">
      <div className="panel-heading"><div className="flex items-center gap-2"><Activity size={15} className="muted" /><h2 id="location-exposure-title">Modeled exposure</h2></div><span className="eyebrow muted">{result.location.name}</span></div>
      <div className="metric-row">
        <Metric primary value={x.annual_expected_hours} source={mapped} unit="h/yr" label="Expected site exposure" />
        <Metric value={x.term_expected_hours} source={mapped} unit="hours" label="Full-term exposure" />
        <Metric value={x.annual_energy_mwh} source={mapped} unit="MWh/yr" label="Energy exposure" />
      </div>
      <div className="chart-section">
        <div className="chart-heading"><div><h3>Exposure over the contract term</h3><p>Fixed historical comparison · expected hours</p></div></div>
        <div className="chart-legend"><span><i className="legend-line" />Expected site exposure</span></div>
        <div className="axis-caption">MODELED EXPOSURE · H/YR</div>
        <div className="fan-chart" role="img" aria-label={`Expected exposure: ${numeric.format(x.annual_expected_hours)} hours per year for ${inputs.contract_years} years. Stationary historical comparison.`}>
          <ResponsiveContainer width="100%" height="100%" minWidth={0}><ComposedChart data={years} margin={{ top: 15, right: 18, bottom: 12, left: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="year" allowDecimals={false} tickLine={false} stroke="var(--text-muted)" />
            <YAxis domain={[0, Math.max(100, Math.ceil(x.annual_expected_hours / 100) * 100)]} tickLine={false} axisLine={false} width={44} stroke="var(--text-muted)" />
            <Line type="linear" dataKey="hours" stroke="var(--text-primary)" strokeWidth={2} dot={years.length === 1} isAnimationActive={false} />
          </ComposedChart></ResponsiveContainer>
        </div>
        <div className="chart-bottom"><span className="eyebrow muted">CONTRACT YEAR</span><Sourced value={x.regional_expected_hours} source={source} label="Regional exposure source">Regional baseline: {numeric.format(x.regional_expected_hours)} h/yr</Sourced></div>
      </div>
      <div className="panel-footnote"><CircleHelp size={13} /><span>Expected hours are a historical comparison. This model does not produce P50/P90/P99 outcomes or predict actual cutoffs.</span></div>
      {result.location_data_note && <div className="panel-footnote"><span>{result.location_data_note}</span></div>}
      <details className="location-evidence"><summary>Model &amp; evidence</summary><p>{result.model_version} · {result.location.latitude}, {result.location.longitude}</p><p>{result.limitations.join(' ')}</p><span>Data sources <SourceInfo value={result.model_version} source={source} description={JSON.stringify(result.provenance)} label="Location estimate provenance" /></span></details>
    </section>
    <section className="panel economics-panel" aria-labelledby="location-economics-title">
      <div className="panel-heading"><div className="flex items-center gap-2"><span className="dollar-icon">$</span><h2 id="location-economics-title">Connection economics</h2></div></div>
      <p className="economics-mock-note">Expected exposure with your economic assumptions. Tail-risk outcomes are unavailable.</p>
      <div className="economics-flow">
        <div className="economics-row"><div><span className="economics-label">Interruptible capacity</span><span className="economics-detail">Load × flexibility split</span></div><span>{sourced(e.interruptible_mw, 'interruptible_mw')} <small>MW</small></span></div>
        {inputs.vpp_solar_homes > 0 && <div className="sustainability-gain-block">
          <div className="economics-row sustainability-row"><div><span className="economics-label text-teal">VPP sustainability offset</span><span className="economics-detail">Assumed solar + battery dispatch</span></div><span>{sourced(e.vpp_offset_mw, 'vpp_offset_mw')} <small>MW</small></span></div>
          <div className="economics-row sustainability-row"><div><span className="economics-label">VPP arbitrage revenue</span><span className="economics-detail">Expected exposure path</span></div><span>{sourced(e.vpp_annual_revenue_usd, 'vpp_annual_revenue_usd', money)} <small>/yr</small></span></div>
        </div>}
        <div className="flow-connector"><ArrowDownRight size={14} /><span>Flexible load − VPP support</span></div>
        <div className="economics-row"><div><span className="economics-label">Net interruptible load</span></div><span>{sourced(e.net_interruptible_mw, 'net_interruptible_mw')} <small>MW</small></span></div>
        <div className="flow-connector"><ArrowDownRight size={14} /><span>Expected exposure × compute density</span></div>
        <div className="economics-row loss-row"><div><span className="economics-label">Expected interruption cost</span><span className="economics-detail">Annual equivalent</span></div><span className="text-amber">{sourced(e.annual_cost_usd, 'annual_cost_usd', money)} <small>/yr</small></span></div>
      </div>
      <div className="term-ledger"><div className="ledger-heading">OVER YOUR {inputs.contract_years}-YEAR TERM</div>
        <div><span>Earlier-access contribution</span>{sourced(e.early_access_value_usd, 'early_access_value_usd', money)}</div>
        <div><span>Expected interruption cost</span>{sourced(-e.term_cost_usd, 'negative term_cost_usd', money)}</div>
        <div className="ledger-net"><span>Net value vs. waiting</span>{sourced(e.net_value_usd, 'net_value_usd', money)}</div>
      </div>
      <div className="decision-readout"><div className="decision-icon"><ArrowRight size={22} /></div><div><span className="eyebrow">EXPECTED BALANCE</span><h3>{e.net_value_usd >= 0 ? 'Positive expected value' : 'Negative expected value'}</h3><p>Compares expected costs with earlier-access contribution. Upper-tail risk has not been estimated.</p></div></div>
      <div className="break-even-row"><span>Break-even modeled exposure</span><strong>{e.break_even_hours === null ? 'No modeled net cost' : <>{sourced(e.break_even_hours, 'break_even_hours')} <small>h/yr</small></>}</strong></div>
    </section>
  </div>;
}
