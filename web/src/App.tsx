import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, Check, ChevronDown, CircleHelp, Download, Gauge, MapPin, RotateCcw, SlidersHorizontal, Unplug, Zap } from 'lucide-react';
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ScenarioProvider, useScenario } from './ScenarioContext';
import { Sourced, SourceInfo, SourcedTick } from './components/Sourced';
import { deriveScenario, mockResponse, type ScenarioInputs, type Source, type SourcedValue } from './model';

const numeric = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const money = (value: number) => `${value < 0 ? '−' : ''}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`;
const signedMoney = (value: number) => `${value >= 0 ? '+' : '−'}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`;
const fixed = (value: number) => value.toFixed(2);
const uiSource = (ref: string): Source => ({ source_type: 'assumption', ref: `mock://display/${ref}` });

function Value({ datum, className, format = numeric.format }: { datum: SourcedValue; className?: string; format?: (value: number) => string }) {
  return <Sourced value={datum.value} source={datum} className={className} format={format} />;
}

function Percentile({ value }: { value: number }) {
  return <Sourced value={value} source={uiSource(`percentile/${value}; statistical percentile label, not a site guarantee`)} animate={false}>p{value}</Sourced>;
}

type NumericKey = Exclude<keyof ScenarioInputs, 'location_id'>;
function NumberField({ name, label, unit, min, max, step = 1, icon, compact = false }: {
  name: NumericKey; label: string; unit: string; min: number; max: number; step?: number; icon?: ReactNode; compact?: boolean;
}) {
  const { inputs, update, sourceFor } = useScenario();
  const [draft, setDraft] = useState(String(inputs[name]));
  useEffect(() => setDraft(String(inputs[name])), [inputs, name]);
  const source = sourceFor(name);
  const isDraft = draft !== String(inputs[name]);
  const visibleValue = isDraft ? draft : inputs[name];
  const visibleSource = isDraft ? { source_type: 'assumption' as const, ref: `user://scenario/${name}/unapplied-draft; outputs retain last valid input until committed` } : source;
  const commit = (text: string, finish = false) => {
    setDraft(text);
    const value = Number(text);
    if (text.trim() && Number.isFinite(value)) {
      if (finish || (value >= min && value <= max && (step !== 1 || Number.isInteger(value)))) {
        const next = Math.min(max, Math.max(min, step === 1 ? Math.round(value) : value));
        update(name, next);
        if (finish) setDraft(String(next));
      }
    } else if (finish) setDraft(String(inputs[name]));
  };
  return <div className={`number-field ${compact ? 'compact-field' : ''}`}>
    <div className="field-label"><label htmlFor={name}>{icon}{label}</label><SourceInfo value={visibleValue} source={visibleSource} label={`${label} provenance`} /></div>
    <div className="input-with-unit">
      <input id={name} type="number" inputMode="decimal" min={min} max={max} step={step} value={draft}
        onChange={event => commit(event.target.value)} onBlur={event => commit(event.target.value, true)}
        onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }}
        title={JSON.stringify({ value: visibleValue, source_type: visibleSource.source_type, ref: visibleSource.ref })} aria-describedby={`${name}-unit`} />
      <span id={`${name}-unit`} className="input-unit">{unit}</span>
    </div>
  </div>;
}

function Header() {
  const { inputs, result, reset } = useScenario();
  const [exported, setExported] = useState(false);
  useEffect(() => { if (exported) { const timer = setTimeout(() => setExported(false), 2400); return () => clearTimeout(timer); } }, [exported]);
  function exportScenario() {
    const blob = new Blob([JSON.stringify({ mode: 'illustrative', inputs, result, fixture: mockResponse }, null, 2)], { type: 'application/json' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'headroom-scenario.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    setExported(true);
  }
  return <>
    <header className="app-header">
      <a href="#main" className="brand" aria-label="Headroom analysis workspace"><span className="brand-mark"><Zap size={20} strokeWidth={2.5} /></span>HEADROOM<span className="brand-divider" /><span className="brand-subtitle">GRID ACCESS INTELLIGENCE</span></a>
      <div className="header-status"><span className="status-dot" />LOCAL WORKSPACE<span className="header-divider" /><span className="fixture-badge">ILLUSTRATIVE DATA</span></div>
    </header>
    <div className="workspace-heading">
      <div><div className="eyebrow breadcrumb">SPP <span>/</span> FLEXIBLE INTERCONNECTION</div><h1>What is earlier power worth?</h1><p>Explore the trade between earlier grid access and interruption exposure.</p></div>
      <div className="workspace-actions"><button className="button button-quiet" onClick={reset}><RotateCcw size={14} />Reset</button><button className="button" onClick={exportScenario}>{exported ? <Check size={14} /> : <Download size={14} />}{exported ? 'Exported' : 'Export scenario'}</button><span className="sr-only" role="status">{exported ? 'Scenario JSON exported.' : ''}</span></div>
    </div>
  </>;
}

function Inputs() {
  const { inputs, update, sourceFor } = useScenario();
  return <section className="inputs-bar" aria-label="Connection inputs">
    <div className="location-field">
      <div className="field-label"><label htmlFor="location"><MapPin size={13} />SPP LOCATION</label><SourceInfo value={inputs.location_id} source={sourceFor('location_id')} label="Location provenance" /></div>
      <div className="select-wrap"><select id="location" value={inputs.location_id} onChange={event => update('location_id', event.target.value)}>{mockResponse.locations.map(location => <option key={location.id} value={location.id}>{location.label}</option>)}</select><ChevronDown size={15} /></div>
      <span className="field-note">Illustrative node · no site-specific grid data</span>
    </div>
    <NumberField name="load_mw" label="LOAD SIZE" unit="MW" min={1} max={2000} icon={<Zap size={13} />} />
    <NumberField name="contract_years" label="CONTRACT TERM" unit="years" min={1} max={20} icon={<Activity size={13} />} />
    <NumberField name="flexibility_percent" label="FLEXIBILITY SPLIT" unit="% interruptible" min={0} max={100} icon={<SlidersHorizontal size={13} />} />
  </section>;
}

function ExposureControl() {
  const { inputs, result, update, sourceFor } = useScenario();
  const crossover = result.economics.break_even_site_exposure;
  const marker = crossover.value !== null && crossover.value >= 0 && crossover.value <= 1 ? crossover.value : null;
  return <section className="exposure-control" aria-labelledby="exposure-label">
    <div className="exposure-copy">
      <div className="flex items-center gap-2"><span className="assumption-badge">USER ASSUMPTION</span><Gauge size={15} className="text-amber" /></div>
      <h2 id="exposure-label">Site exposure factor</h2>
      <p>Public grid stress does not establish a specific site’s actual curtailment. <strong>You set the mapping.</strong></p>
    </div>
    <div className="exposure-slider-area">
      <div className="slider-readout"><span>Share of system stress mapped to this site</span><Sourced value={inputs.site_exposure} source={sourceFor('site_exposure')} format={fixed} className="exposure-number" /></div>
      <div className="slider-track-wrap">
        <input type="range" min={0} max={1} step={0.01} value={inputs.site_exposure} onChange={event => update('site_exposure', Number(event.target.value))}
          aria-label="Site exposure factor" aria-describedby="exposure-explanation" aria-valuetext={`${inputs.site_exposure.toFixed(2)}, user-set assumption`}
          style={{ '--range-progress': `${inputs.site_exposure * 100}%` } as CSSProperties} />
        {marker !== null && <span className="break-even-marker" style={{ left: `calc(10px + (100% - 20px) * ${marker})` }} title="Economic break-even under current assumptions" />}
      </div>
      <div className="slider-endpoints"><span><Sourced value={0} source={uiSource('site_exposure/min')} format={v => v.toFixed(1)} animate={false} /> No exposure</span><span>Full modeled exposure <Sourced value={1} source={uiSource('site_exposure/max')} format={v => v.toFixed(1)} animate={false} /></span></div>
      <div className="slider-caption" id="exposure-explanation"><span className="tiny-diamond" />{marker !== null ? <span>Decision break-even at <Value datum={crossover as SourcedValue} format={fixed} /> under these assumptions</span> : <span>No decision crossover within this slider range</span>}</div>
    </div>
  </section>;
}

type AnnualPoint = ReturnType<typeof deriveScenario>['annual_series'][number];
type ChartRow = { year: number; band: [number, number]; median: number; original: AnnualPoint };
function FanTooltip({ active, row }: { active?: boolean; row?: ChartRow }) {
  if (!active || !row) return null;
  return <div className="chart-tooltip"><div className="eyebrow">CONTRACT YEAR <Value datum={row.original.year} format={integer.format} /></div>
    <div><span><Percentile value={90} /> modeled exposure</span><span><Value datum={row.original.p90} /> h/yr</span></div>
    <div className="text-mint"><span><Percentile value={50} /> modeled exposure</span><span><Value datum={row.original.p50} /> h/yr</span></div>
    <div><span><Percentile value={10} /> modeled exposure</span><span><Value datum={row.original.p10} /> h/yr</span></div>
    <small>Illustrative quantiles · hover or tap a value for its source</small>
  </div>;
}

function ExposurePanel() {
  const { result, inputs } = useScenario();
  const [tableOpen, setTableOpen] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReducedMotion(query.matches);
    sync(); query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);
  const data: ChartRow[] = result.annual_series.map(row => ({ year: row.year.value, band: [row.p10.value, row.p90.value], median: row.p50.value, original: row }));
  const baseline = deriveScenario({ ...inputs, site_exposure: 1 });
  const maximum = Math.ceil(Math.max(...baseline.annual_series.map(row => row.p90.value)) / 100) * 100;
  const yTicks = Array.from({ length: 5 }, (_, i) => maximum * i / 4);
  return <section className="panel exposure-panel" aria-labelledby="exposure-panel-title">
    <div className="panel-heading"><div className="flex items-center gap-2"><Activity size={15} className="text-mint" /><h2 id="exposure-panel-title">Modeled exposure</h2></div><span className="eyebrow muted">HOURS / YEAR</span></div>
    <div className="metric-row">
      {([['p50', 50, 'Median scenario'], ['p90', 90, 'Upper-tail scenario'], ['p99', 99, 'Extreme-tail scenario']] as const).map(([key, percentile, label]) => <div className={`metric metric-${key}`} key={key}>
        <div className="metric-label"><Percentile value={percentile} /><span>{label}</span></div>
        <div className="metric-number"><Value datum={result.annual_exposure[key]} format={numeric.format} /><span className="metric-unit">h/yr</span></div>
      </div>)}
    </div>
    <div className="chart-section">
      <div className="chart-heading"><div><h3>Exposure over the contract term</h3><p>Illustrative Monte Carlo fan · fixed annual quantiles</p></div><div className="chart-legend"><span><i className="legend-line" /><Percentile value={50} /></span><span><i className="legend-band" /><Percentile value={10} />–<Percentile value={90} /></span></div></div>
      <div className="axis-caption">MODELED EXPOSURE · H/YR</div>
      <div className="fan-chart" role="group" aria-label="Annual modeled exposure fan chart. Median line and p10 to p90 band. Exact sourced values are available in the annual data table below.">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <ComposedChart data={data} margin={{ top: 15, right: 18, bottom: 12, left: 8 }} accessibilityLayer>
            <defs><linearGradient id="fan-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#a0bea0" stopOpacity={0.23} /><stop offset="100%" stopColor="#8cb896" stopOpacity={0.035} /></linearGradient></defs>
            <CartesianGrid vertical={false} stroke="#303832" strokeDasharray="3 5" />
            <XAxis dataKey="year" tickLine={false} axisLine={{ stroke: '#3b433c' }} interval="preserveStartEnd" minTickGap={35} height={30} tick={<SourcedTick source={uiSource('contract_year; ordinal year in selected contract')} axis="x" />} />
            <YAxis domain={[0, maximum]} ticks={yTicks} axisLine={false} tickLine={false} width={44} tick={<SourcedTick source={uiSource('axis/hours_per_year; chart scale, not an observation')} axis="y" />} />
            <Tooltip content={({ active, payload }) => <FanTooltip active={active} row={payload?.[0]?.payload as ChartRow | undefined} />} cursor={{ stroke: '#9eac9e', strokeDasharray: '3 4' }} wrapperStyle={{ pointerEvents: 'auto', zIndex: 30 }} />
            <Area type="linear" dataKey="band" stroke="#718c72" strokeOpacity={0.55} fill="url(#fan-fill)" activeDot={false} animationDuration={280} isAnimationActive={!reducedMotion} />
            <Line type="linear" dataKey="median" stroke="#c2e59e" strokeWidth={2.5} dot={data.length === 1 ? { r: 4 } : false} activeDot={{ r: 4, fill: '#c2e59e', stroke: '#101412', strokeWidth: 2 }} animationDuration={280} isAnimationActive={!reducedMotion} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-bottom"><span className="eyebrow muted">CONTRACT YEAR</span><button onClick={() => setTableOpen(!tableOpen)} className="text-button" aria-expanded={tableOpen} aria-controls="annual-values">{tableOpen ? 'Hide annual values' : 'Inspect annual values'}<ChevronDown size={12} className={tableOpen ? 'rotate-180' : ''} /></button></div>
      {tableOpen && <div id="annual-values" className="annual-table-wrap"><table className="annual-table"><caption className="sr-only">Sourced annual modeled exposure in hours per year</caption><thead><tr><th>Year</th><th><Percentile value={10} /> h/yr</th><th><Percentile value={50} /> h/yr</th><th><Percentile value={90} /> h/yr</th></tr></thead><tbody>{result.annual_series.map(row => <tr key={row.year.value}><td><Value datum={row.year} /></td><td><Value datum={row.p10} /></td><td><Value datum={row.p50} /></td><td><Value datum={row.p90} /></td></tr>)}</tbody></table></div>}
    </div>
    <div className="panel-footnote"><CircleHelp size={13} /><span>Annual summaries average each percentile across the selected term. They do not describe the distribution of total contract exposure.</span></div>
  </section>;
}

function EconomicsPanel() {
  const { result, inputs, sourceFor } = useScenario();
  const { economics: e, decision } = result;
  const state = decision === 'worth it' ? 'positive' : decision === 'not worth it' ? 'negative' : 'neutral';
  return <section className={`panel economics-panel decision-${state}`} aria-labelledby="economics-title">
    <div className="panel-heading"><div className="flex items-center gap-2"><span className="dollar-icon">$</span><h2 id="economics-title">The economics</h2></div><span className="eyebrow muted">MEDIAN-PATH SCENARIO</span></div>
    <div className="economics-flow">
      <div className="economics-row"><div><span className="economics-label">Interruptible capacity</span><span className="economics-detail">Load × flexibility split</span></div><span><Value datum={e.interruptible_mw} /> <small>MW</small></span></div>
      <div className="flow-connector"><ArrowDownRight size={14} /><span>Modeled exposure × compute density</span></div>
      <div className="economics-row"><div><span className="economics-label">Lost GPU-hours</span><span className="economics-detail">Annual equivalent · median path</span></div><span><Value datum={e.annual_lost_gpu_hours} format={integer.format} /> <small>/yr</small></span></div>
      <div className="flow-connector"><ArrowDownRight size={14} /><span>Lost GPU-hours × value per GPU-hour</span></div>
      <div className="economics-row loss-row"><div><span className="economics-label">Modeled interruption cost</span><span className="economics-detail">Annual equivalent</span></div><span className="text-amber"><Value datum={e.annual_loss_usd} format={money} /> <small>/yr</small></span></div>
    </div>
    <div className="term-ledger">
      <div className="ledger-heading">OVER YOUR <Sourced value={inputs.contract_years} source={sourceFor('contract_years')} format={integer.format} />-YEAR TERM</div>
      <div><span>Earlier-access contribution</span><Value datum={e.early_access_value_usd} format={signedMoney} className="text-mint" /></div>
      <div><span>Modeled interruption cost</span><Value datum={{ ...e.term_loss_usd, value: -e.term_loss_usd.value, ref: `${e.term_loss_usd.ref}; display_as_ledger_debit = -term_loss_usd` }} format={money} /></div>
      <div className="ledger-net"><span>Net value vs. waiting</span><Value datum={e.net_value_usd} format={signedMoney} /></div>
    </div>
    <div className="decision-readout" role="status" aria-live="polite" aria-atomic="true">
      <div className="decision-icon">{state === 'positive' ? <ArrowUpRight size={22} /> : state === 'negative' ? <ArrowDownRight size={22} /> : <ArrowRight size={22} />}</div>
      <div><span className="eyebrow">UNDER THESE ASSUMPTIONS</span><h3>{decision}</h3><p>{state === 'positive' ? 'Earlier-access contribution exceeds modeled losses.' : state === 'negative' ? 'Modeled losses exceed earlier-access contribution.' : 'The modeled trade is near economic break-even.'}</p></div>
    </div>
    <div className="break-even-row"><span>Break-even modeled exposure</span><strong>{e.break_even_exposure_hours.value === null ? 'No modeled cost' : <><Value datum={e.break_even_exposure_hours as SourcedValue} /> <small>h/yr</small></>}</strong></div>
  </section>;
}

function Assumptions() {
  const { inputs, result, sourceFor } = useScenario();
  return <section className="assumptions-panel" aria-labelledby="assumptions-title">
    <div className="assumptions-heading"><div className="flex items-center gap-2"><SlidersHorizontal size={14} /><h2 id="assumptions-title">The assumptions behind the dollars</h2></div><span className="eyebrow text-amber">ALL EDITABLE · ALL ILLUSTRATIVE</span></div>
    <div className="assumption-fields">
      <NumberField compact name="firm_wait_years" label="EARLIER ACCESS" unit="years" min={0} max={20} step={0.25} />
      <NumberField compact name="gpu_per_mw" label="COMPUTE DENSITY" unit="GPU / MW" min={1} max={2000} />
      <NumberField compact name="gpu_hour_value_usd" label="LOST COMPUTE VALUE" unit="$ / GPU-h" min={0} max={100} step={0.1} />
      <NumberField compact name="early_margin_usd_per_mw_year" label="EARLY OPERATING MARGIN" unit="$ / MW / yr" min={0} max={10000000} step={1000} />
    </div>
    <div className="assumption-notes"><p><span className="note-label">VALUE OF TIME</span>Earlier contribution = total load × operating margin × earlier-access years, capped at your contract term. Interruption losses apply to the interruptible share across the full term.</p><p><span className="note-label">DECISION RULE</span>“Close call” means net value is within <Sourced value={result.decision_policy.close_call_fraction.value * 100} source={{ ...result.decision_policy.close_call_fraction, ref: `${result.decision_policy.close_call_fraction.ref}; display_percent = fraction * 100` }} format={integer.format} />% of earlier-access contribution. This scenario excludes discounting, restart overhead, and SLA penalties.</p></div>
    {inputs.firm_wait_years > inputs.contract_years && <p className="assumption-notice">Earlier-access contribution is capped at <Sourced value={inputs.contract_years} source={sourceFor('contract_years')} /> years for this contract.</p>}
    <div className="honesty-note"><CircleHelp size={15} /><p><strong>A scenario, not a site forecast.</strong> All values are illustrative assumptions. Public grid data can establish system stress; local transmission headroom determines whether a specific site would be curtailed. A flexible share does not establish eligibility for a particular tariff.</p></div>
  </section>;
}

function Workspace() {
  return <div className="app-shell"><a className="skip-link" href="#main">Skip to analysis</a><Header /><main id="main"><Inputs /><ExposureControl /><div className="results-grid"><ExposurePanel /><EconomicsPanel /></div><Assumptions /></main><footer><span className="flex items-center gap-2"><Unplug size={12} />OFFLINE-READY FIXTURE · NO LIVE GRID DATA</span><span>Every number has a source. Hover, focus, or click its <span className="source-example">a</span> tag.</span></footer></div>;
}

export default function App() { return <ScenarioProvider><Workspace /></ScenarioProvider>; }
