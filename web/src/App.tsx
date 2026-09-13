import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, Check, ChevronDown, CircleHelp, Download, Gauge, MapPin, RotateCcw, SlidersHorizontal, Unplug, Zap, CloudSun } from 'lucide-react';
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ScenarioProvider, useScenario } from './ScenarioContext';
import { ScenarioComparison } from './components/ScenarioComparison';
import { ZoneLeaderboard } from './components/ZoneLeaderboard';
import { Sourced, SourceInfo, SourcedTick } from './components/Sourced';
import { TransparencyPanel } from './components/TransparencyPanel';
import { SensitivityPanel } from './components/SensitivityPanel';
import { ConfidenceBadge, type ConfidenceEstimate } from './components/ConfidenceBadge';
import { MockLabel, isMockSource } from './components/MockLabel';
import { useChangeMotion } from './hooks/useChangeMotion';
import { deriveScenario, mockResponse, toEstimateRequest, type ScenarioInputs, type Source, type SourcedValue } from './model';

const ExposureSurface = lazy(() => import('./components/ExposureSurface'));
class SurfaceBoundary extends Component<{ children: ReactNode; onUnavailable: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onUnavailable(); }
  render() { return this.state.failed ? null : this.props.children; }
}

const numeric = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const money = (value: number) => `${value < 0 ? '−' : ''}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`;
const signedMoney = (value: number) => `${value >= 0 ? '+' : '−'}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`;
const fixed = (value: number) => value.toFixed(2);
const uiSource = (ref: string): Source => ({ source_type: 'assumption', ref: `mock://display/${ref}` });

function Value({ datum, className, format = numeric.format }: { datum: SourcedValue; className?: string; format?: (value: number) => string }) {
  return <Sourced value={datum.value} source={datum} className={className} format={format} />;
}

function ExposureValue({ datum, confidence }: { datum: SourcedValue; confidence: ConfidenceEstimate }) {
  return <span className="exposure-value-with-confidence"><Value datum={datum} /><ConfidenceBadge confidence={confidence} compact /></span>;
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
    <div className="field-label"><label htmlFor={name}>{icon}{label}</label><span className="field-source">{compact && <MockLabel sources={[visibleSource]} />}<SourceInfo value={visibleValue} source={visibleSource} label={`${label} provenance`} /></span></div>
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
  const { inputs, result, sensitivity, reset, mode, status } = useScenario();
  const [exported, setExported] = useState(false);
  useEffect(() => { if (exported) { const timer = setTimeout(() => setExported(false), 2400); return () => clearTimeout(timer); } }, [exported]);
  function exportScenario() {
    const blob = new Blob([JSON.stringify({ mode, status, response_origin: status === 'api' ? 'api' : 'local_mock', request: toEstimateRequest(inputs), response: result.canonical_response, local_assumptions: result.inputs, result, sensitivity }, null, 2)], { type: 'application/json' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'fluxline-scenario.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    setExported(true);
  }
  return <>
    <header className="app-header">
      <a href="/" className="brand" aria-label="Fluxline home"><span className="brand-mark" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 18 18" fill="none"><path d="M3 2V16M15 2V16M3 9H15M7 5V13M11 5V13" stroke="currentColor" strokeWidth="1.3" /></svg></span>Fluxline<span className="brand-divider" /><span className="brand-subtitle">INTERCONNECTION RISK</span></a>
      <div className="header-status"><span className="status-dot" />{status === 'api' ? 'API CONNECTED' : status === 'loading' ? 'API PENDING' : status === 'fallback' ? 'LOCAL FALLBACK' : 'LOCAL WORKSPACE'}</div>
    </header>
    <div className="workspace-heading">
      <div><div className="eyebrow breadcrumb">SPP <span>/</span> SCENARIO ANALYSIS</div><h1>Flexible connection analysis</h1><p>Earlier grid access, modeled interruption exposure, and the cost of waiting.</p></div>
      <div className="workspace-actions"><button className="button button-quiet" onClick={reset}><RotateCcw size={14} />Reset</button><button className="button" onClick={exportScenario}>{exported ? <Check size={14} /> : <Download size={14} />}{exported ? 'Exported' : 'Export scenario'}</button><span className="sr-only" role="status">{exported ? 'Scenario JSON exported.' : ''}</span></div>
    </div>
    <EstimateConnection />
  </>;
}

function EstimateConnection() {
  const { mode, status, error, retry, chooseMode, modeNote } = useScenario();
  const message = status === 'api'
    ? 'API connected · current inputs synchronized.'
    : status === 'loading'
      ? 'Waiting for API · current inputs shown as a local mock preview.'
      : status === 'fallback'
        ? error
        : 'Local mock · instantaneous, no backend required.';
  return <section className={`estimate-connection estimate-connection-${status}`} aria-label="Estimate data connection">
    <div className="estimate-mode-controls segment-control" role="group" aria-label="Estimate provider">
      <button className="button button-quiet" aria-pressed={mode === 'api'} onClick={() => chooseMode('api')}>Use API defaults</button>
      <button className="button button-quiet" aria-pressed={mode === 'local'} onClick={() => chooseMode('local')}>Local mock</button>
    </div>
    <div className="estimate-connection-copy"><p role="status">{message}</p>{modeNote && <p className="estimate-mode-note">{modeNote}</p>}<small>Editing economics switches to Local mock. Use API defaults resets those economic inputs.</small></div>
    {status === 'fallback' && <button className="button" onClick={retry}>Retry API</button>}
  </section>;
}

interface TelemetryStation {
  id: string
  name: string
  callsign: string
  coords: string
  weight: string
  db: string
}

const telemetryStations: Record<string, TelemetryStation[]> = {
  'SPP_SYSTEM': [
    { id: 'okc', name: 'Will Rogers World Airport, Oklahoma City, OK', callsign: 'KOKC', coords: '35.47° N, 97.52° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
    { id: 'ict', name: 'Eisenhower National Airport, Wichita, KS', callsign: 'KICT', coords: '37.69° N, 97.34° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
    { id: 'ama', name: 'Rick Husband Intl Airport, Amarillo, TX', callsign: 'KAMA', coords: '35.22° N, 101.83° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
    { id: 'oma', name: 'Eppley Airfield, Omaha, NE', callsign: 'KOMA', coords: '41.26° N, 95.94° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
    { id: 'fsd', name: 'Joe Foss Field, Sioux Falls, SD', callsign: 'KFSD', coords: '43.55° N, 96.73° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
    { id: 'bis', name: 'Bismarck Municipal Airport, Bismarck, ND', callsign: 'KBIS', coords: '46.81° N, 100.78° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
  ],
  'spp-wichita-demo': [
    { id: 'ict', name: 'Eisenhower National Airport, Wichita, KS', callsign: 'KICT', coords: '37.69° N, 97.34° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
  ],
  'spp-oklahoma-city-demo': [
    { id: 'okc', name: 'Will Rogers World Airport, Oklahoma City, OK', callsign: 'KOKC', coords: '35.47° N, 97.52° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
  ],
  'spp-lincoln-demo': [
    { id: 'lnk', name: 'Lincoln Airport, Lincoln, NE', callsign: 'KLNK', coords: '40.85° N, 96.75° W', weight: '1.0x', db: 'ERA5 / ECMWF' },
  ],
}

const getTelemetryAscii = (locationId: string) => {
  if (locationId === 'SPP_SYSTEM') {
    return `
      [KBIS: Bismarck, ND] ----- (1.0x) ----\\
      [KFSD: Sioux Falls] ------ (1.0x) -----\\
      [KOMA: Omaha, NE] -------- (1.0x) ------+---> [ SPP Climate Proxy Array ]
      [KICT: Wichita, KS] ------ (1.0x) ------|
      [KAMA: Amarillo, TX] ----- (1.0x) -----/
      [KOKC: Oklahoma City] ---- (1.0x) ----/
    `.trim()
  }
  const callsign = telemetryStations[locationId]?.[0]?.callsign || 'WTHR'
  const name = telemetryStations[locationId]?.[0]?.name.split(',')[0] || 'Local station'
  return `
      [${callsign}: ${name}] === (1.0x Weight) ===> [ Model Climate Input ]
  `.trim()
}

function Inputs() {
  const { inputs, update, sourceFor } = useScenario();
  const [showTelemetry, setShowTelemetry] = useState(false);

  const stations = telemetryStations[inputs.location_id] || [];
  const ascii = getTelemetryAscii(inputs.location_id);

  return <div className="inputs-wrapper">
    <section className="inputs-bar" aria-label="Connection inputs">
      <div className="location-field">
        <div className="field-label"><label htmlFor="location"><MapPin size={13} />SPP LOCATION</label><SourceInfo value={inputs.location_id} source={sourceFor('location_id')} label="Location provenance" /></div>
        <div className="select-wrap"><select id="location" value={inputs.location_id} onChange={event => update('location_id', event.target.value)}>{mockResponse.locations.map(location => <option key={location.id} value={location.id}>{location.label}</option>)}</select><ChevronDown size={15} /></div>
        <span className="field-note">{inputs.location_id === 'SPP_SYSTEM' ? 'System aggregate · no site-specific grid data' : 'Illustrative node · no site-specific grid data'}</span>
        <button
          type="button"
          className="telemetry-toggle-btn"
          onClick={() => setShowTelemetry(open => !open)}
          aria-expanded={showTelemetry}
          aria-controls="telemetry-drawer"
        >
          <Activity size={10} aria-hidden="true" />
          {showTelemetry ? 'Hide telemetry details' : 'Inspect climate telemetry'}
        </button>
      </div>
      <NumberField name="load_mw" label="LOAD SIZE" unit="MW" min={1} max={2000} icon={<Zap size={13} />} />
      <NumberField name="contract_years" label="CONTRACT TERM" unit="years" min={1} max={7} icon={<Activity size={13} />} />
      <NumberField name="flexibility_percent" label="FLEXIBILITY SPLIT" unit="% interruptible" min={0} max={100} icon={<SlidersHorizontal size={13} />} />
      <NumberField name="vpp_solar_homes" label="VPP ORCHESTRATION" unit="solar homes" min={0} max={10000} icon={<CloudSun size={13} className="text-teal" />} />
    </section>
    
    {showTelemetry && <section id="telemetry-drawer" className="telemetry-drawer" aria-label="Climate telemetry nodes">
      <div className="telemetry-drawer-header">
        <div>
          <span className="eyebrow text-teal">MODEL TEMPERATURE CORRELATION</span>
          <h4>Climate reanalysis telemetry station nodes</h4>
        </div>
        <p>The exposure model correlates historical regional temperature profiles with SPP grid-stress incidents. Temperature features are extracted from the ECMWF ERA5 reanalysis dataset via Open-Meteo.</p>
      </div>
      <div className="telemetry-drawer-body">
        <div className="telemetry-grid">
          <table className="telemetry-table-list">
            <caption>ACTIVE GEOGRAPHICAL STATION PROXIES</caption>
            <thead>
              <tr>
                <th scope="col">Station Call</th>
                <th scope="col">Location Name</th>
                <th scope="col">Coordinates</th>
                <th scope="col">Weight</th>
                <th scope="col">Telemetry DB</th>
              </tr>
            </thead>
            <tbody>
              {stations.map(station => {
                const src: Source = { source_type: 'data', ref: `mock://weather-telemetry/station/${station.id}; coordinates resolved via Open-Meteo geocoding` };
                return <tr key={station.id}>
                  <td><strong>{station.callsign}</strong></td>
                  <td>{station.name}</td>
                  <td>
                    <SourceInfo value={station.coords} source={src} label={`${station.callsign} coordinates`} displayText={station.coords} />
                  </td>
                  <td>{station.weight}</td>
                  <td><span className="telemetry-db-badge">{station.db}</span></td>
                </tr>
              })}
            </tbody>
          </table>
          
          <div className="telemetry-diagram-panel">
            <span className="eyebrow muted text-center block mb-2">SCHEMATIC DIAGRAM</span>
            <pre className="telemetry-ascii">{ascii}</pre>
          </div>
        </div>
      </div>
    </section>}
  </div>;
}

function ExposureControl() {
  const { inputs, result, update, sourceFor } = useScenario();
  const crossover = result.economics.break_even_site_exposure;
  const crossoverSources = [crossover, result.annual_exposure.p50];
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
        <span className="slider-progress" style={{ width: `calc(10px + (100% - 20px) * ${inputs.site_exposure})` }} aria-hidden="true" />
        <input type="range" min={0} max={1} step={0.01} value={inputs.site_exposure} onChange={event => update('site_exposure', Number(event.target.value))}
          aria-label="Site exposure factor" aria-describedby="exposure-explanation" aria-valuetext={`${inputs.site_exposure.toFixed(2)}, user-set assumption`} />
        {marker !== null && <span className="break-even-marker" style={{ left: `calc(10px + (100% - 20px) * ${marker})` }} title={`${crossoverSources.some(isMockSource) ? 'Mock ' : ''}Median cost crossover under current assumptions`} />}
      </div>
      <div className="slider-endpoints"><span><Sourced value={0} source={uiSource('site_exposure/min')} format={v => v.toFixed(1)} animate={false} /> No exposure</span><span>Full modeled exposure <Sourced value={1} source={uiSource('site_exposure/max')} format={v => v.toFixed(1)} animate={false} /></span></div>
      <div className="slider-caption" id="exposure-explanation"><span className="tiny-diamond" />{marker !== null ? <span>Median cost crossover at <Value datum={crossover as SourcedValue} format={fixed} /> <MockLabel sources={crossoverSources} /> under these assumptions</span> : <span>No cost crossover within this slider range</span>}</div>
    </div>
  </section>;
}

type AnnualPoint = ReturnType<typeof deriveScenario>['annual_series'][number];
type ChartRow = { year: number; band: [number, number]; median: number; upper: number; original: AnnualPoint };
function FanTooltip({ active, row, confidence }: { active?: boolean; row?: ChartRow; confidence: ConfidenceEstimate }) {
  if (!active || !row) return null;
  return <div className="chart-tooltip"><div className="eyebrow">CONTRACT YEAR <Value datum={row.original.year} format={integer.format} /></div>
    <div><span><Percentile value={99} /> modeled exposure · h/yr</span><ExposureValue datum={row.original.p99} confidence={confidence} /></div>
    <div><span><Percentile value={90} /> modeled exposure · h/yr</span><ExposureValue datum={row.original.p90} confidence={confidence} /></div>
    <div><span><Percentile value={50} /> modeled exposure · h/yr</span><ExposureValue datum={row.original.p50} confidence={confidence} /></div>
    <small><MockLabel sources={[row.original.p50, row.original.p90, row.original.p99]} children="Mock quantiles" /> Hover or tap a value for its source</small>
  </div>;
}

function ExposurePanel() {
  const { result, inputs } = useScenario();
  const [tableOpen, setTableOpen] = useState(false);
  const [transparencyOpen, setTransparencyOpen] = useState(false);
  const transparencyTrigger = useRef<HTMLButtonElement>(null);
  const closeTransparency = () => { setTransparencyOpen(false); transparencyTrigger.current?.focus(); };
  const [view, setView] = useState<'surface' | 'fan'>('fan');
  const chartRef = useRef<HTMLDivElement>(null);
  useChangeMotion(chartRef, view);
  const [surfaceUnavailable, setSurfaceUnavailable] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReducedMotion(query.matches);
    sync(); query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);
  const data: ChartRow[] = result.annual_series.map(row => ({ year: row.year.value, band: [row.p50.value, row.p90.value], median: row.p50.value, upper: row.p99.value, original: row }));
  const baseline = deriveScenario({ ...inputs, site_exposure: 1 });
  const maximum = Math.ceil(Math.max(...baseline.annual_series.map(row => row.p99.value), ...result.annual_series.map(row => row.p99.value)) / 100) * 100;
  const yTicks = Array.from({ length: 5 }, (_, i) => maximum * i / 4);
  const showFallback = () => { setSurfaceUnavailable(true); setView('fan'); };
  return <section className="panel exposure-panel" aria-labelledby="exposure-panel-title">
    <div className="panel-heading"><div className="flex items-center gap-2"><Activity size={15} className="muted" /><h2 id="exposure-panel-title">Modeled exposure</h2></div><div className="exposure-panel-actions"><span className="eyebrow muted">HOURS / YEAR</span><button ref={transparencyTrigger} className="button transparency-trigger" aria-expanded={transparencyOpen} aria-controls="transparency-panel" onClick={() => setTransparencyOpen(open => !open)}><CircleHelp size={13} />Model &amp; evidence</button></div></div>
    <div className="metric-row">
      {([['p50', 50, 'Median scenario'], ['p90', 90, 'Upper-tail scenario'], ['p99', 99, 'Extreme-tail scenario']] as const).map(([key, percentile, label]) => <div className={`metric metric-${key}${key === 'p50' ? ' metric-primary' : ''}`} key={key}>
        <div className="metric-label"><Percentile value={percentile} /><span>{label}</span></div>
        <div className="metric-number"><Value datum={result.annual_exposure[key]} format={numeric.format} /><span className="metric-unit">h/yr</span></div>
        <MockLabel sources={[result.annual_exposure[key]]} children="Mock exposure" />
        <ConfidenceBadge confidence={result.confidence} />
      </div>)}
    </div>
    {transparencyOpen && <TransparencyPanel confidence={result.confidence} tariff={result.tariff} siteExposure={result.inputs.site_exposure} onClose={closeTransparency} />}
    <div className="chart-section" ref={chartRef}>
      <div className="chart-heading"><div><h3>Exposure over the contract term</h3><p><MockLabel sources={[result.canonical_response.modeled_exposure.source]} children="Mock quantiles" /> Fixed inputs · no live simulation</p></div><div className="chart-view-controls segment-control" role="group" aria-label="Exposure visualization"><button aria-pressed={view === 'fan'} onClick={() => setView('fan')}>Fan chart</button><button aria-pressed={view === 'surface'} onClick={() => { setSurfaceUnavailable(false); setView('surface'); }}>Surface</button></div></div>
      {surfaceUnavailable && <p className="surface-fallback" role="status">Interactive surface unavailable on this device. Fan chart shown; exact sourced values remain below.</p>}
      {view === 'surface' ? <SurfaceBoundary onUnavailable={showFallback}><Suspense fallback={<div className="surface-loading" role="status">Preparing exposure surface…</div>}><ExposureSurface rows={result.annual_series} maximumHours={maximum} confidence={result.confidence} onUnavailable={showFallback} /></Suspense></SurfaceBoundary> : <>
      <div className="chart-legend fan-legend"><span><i className="legend-line" /><Percentile value={50} /></span><span><i className="legend-band" /><Percentile value={50} />–<Percentile value={90} /></span><span><i className="legend-line legend-upper" /><Percentile value={99} /></span></div>
      <div className="axis-caption">MODELED EXPOSURE · H/YR</div>
      <div className="fan-chart" role="group" aria-label="Annual modeled exposure fan chart. Median line, p50 to p90 band, and p99 line. Exact sourced values are available in the annual data table below.">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <ComposedChart data={data} margin={{ top: 15, right: 18, bottom: 12, left: 8 }} accessibilityLayer>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="year" tickLine={false} axisLine={{ stroke: 'var(--border-strong)' }} interval="preserveStartEnd" minTickGap={35} height={30} tick={<SourcedTick source={uiSource('contract_year; ordinal year in selected contract')} axis="x" />} />
            <YAxis domain={[0, maximum]} ticks={yTicks} axisLine={false} tickLine={false} width={44} tick={<SourcedTick source={uiSource('axis/hours_per_year; chart scale, not an observation')} axis="y" />} />
            <Tooltip content={({ active, payload }) => <FanTooltip active={active} row={payload?.[0]?.payload as ChartRow | undefined} confidence={result.confidence} />} cursor={{ stroke: 'var(--text-muted)', strokeDasharray: '3 4' }} wrapperStyle={{ pointerEvents: 'auto', zIndex: 30 }} />
            <Area type="linear" dataKey="band" stroke="var(--teal)" strokeOpacity={0.65} fill="var(--teal)" fillOpacity={0.12} activeDot={false} animationDuration={320} animationEasing="ease-out" isAnimationActive={!reducedMotion} />
            <Line type="linear" dataKey="median" stroke="var(--text-primary)" strokeWidth={2} dot={data.length === 1 ? { r: 3 } : false} activeDot={{ r: 4, fill: 'var(--text-primary)', stroke: 'var(--bg-base)', strokeWidth: 2 }} animationDuration={320} animationEasing="ease-out" isAnimationActive={!reducedMotion} />
            <Line type="linear" dataKey="upper" stroke="var(--teal)" strokeWidth={1} strokeDasharray="4 4" dot={data.length === 1 ? { r: 3 } : false} activeDot={{ r: 3 }} animationDuration={320} animationEasing="ease-out" isAnimationActive={!reducedMotion} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      </>}
      <div className="chart-bottom"><span className="eyebrow muted">{view === 'fan' ? 'CONTRACT YEAR' : 'SUPPLIED QUANTILES'}</span><button onClick={() => setTableOpen(!tableOpen)} className="text-button" aria-expanded={tableOpen} aria-controls="annual-values">{tableOpen ? 'Hide annual values' : 'Inspect annual values'}<ChevronDown size={12} className={tableOpen ? 'rotate-180' : ''} /></button></div>
      {tableOpen && <div id="annual-values" className="annual-table-wrap"><table className="annual-table"><caption className="sr-only">Sourced annual modeled exposure in hours per year</caption><thead><tr><th>Year</th><th><Percentile value={50} /> h/yr</th><th><Percentile value={90} /> h/yr</th><th><Percentile value={99} /> h/yr</th></tr></thead><tbody>{result.annual_series.map(row => <tr key={row.year.value}><td><Value datum={row.year} /></td><td><ExposureValue datum={row.p50} confidence={result.confidence} /></td><td><ExposureValue datum={row.p90} confidence={result.confidence} /></td><td><ExposureValue datum={row.p99} confidence={result.confidence} /></td></tr>)}</tbody></table></div>}
    </div>
    <div className="panel-footnote"><CircleHelp size={13} /><span>Annual summaries average each percentile across the selected term. They do not describe the distribution of total contract exposure.</span></div>
    <div className="panel-footnote confidence-footnote"><CircleHelp size={13} /><span>Confidence describes support for an estimate, not the chance that the future matches it.{isMockSource(result.confidence.source) && ' The mock confidence signal has not been computed by an ensemble.'}</span></div>
  </section>;
}

function EconomicsPanel() {
  const { result, inputs, sourceFor } = useScenario();
  const { economics: e, decision } = result;
  const economicsSource = result.canonical_response.economics.source;
  const decisionRef = useRef<HTMLHeadingElement>(null);
  useChangeMotion(decisionRef, decision);
  const state = decision === 'worth it' ? 'positive' : decision === 'not worth it' ? 'negative' : 'neutral';
  return <section className={`panel economics-panel decision-${state}`} aria-labelledby="economics-title" aria-describedby={isMockSource(economicsSource) ? 'economics-mock-note' : undefined}>
    <div className="panel-heading"><div className="flex items-center gap-2"><span className="dollar-icon">$</span><h2 id="economics-title">Connection economics</h2></div></div>
    {isMockSource(economicsSource) && <p className="economics-mock-note" id="economics-mock-note"><MockLabel sources={[economicsSource]} children="Mock economics" /> GPU-hours, dollars and break-even use unverified placeholder inputs.</p>}
    <div className="economics-flow">
      <div className="economics-row"><div><span className="economics-label">Interruptible capacity</span><span className="economics-detail">Load × flexibility split</span></div><span><Value datum={e.interruptible_mw} /> <small>MW</small></span></div>
      
      {inputs.vpp_solar_homes > 0 && <div className="sustainability-gain-block">
        <div className="economics-row sustainability-row">
          <div><span className="economics-label text-teal">VPP sustainability offset</span><span className="economics-detail">Residential solar + BESS dispatch</span></div>
          <span className="text-teal">−<Value datum={e.vpp_offset_mw} /> <small>MW</small></span>
        </div>
        <div className="economics-row sustainability-row arbitrage">
          <div><span className="economics-label text-teal">VPP arbitrage revenue</span><span className="economics-detail">Peak scarcity dispatch · median path</span></div>
          <span className="text-teal">+<Value datum={e.vpp_arbitrage_revenue_usd} format={money} /> <small>/yr</small></span>
        </div>
      </div>}

      <div className="flow-connector"><ArrowDownRight size={14} /><span>Net exposure × compute density</span></div>
      <div className="economics-row"><div><span className="economics-label">Net interruptible load</span><span className="economics-detail">Flexible load − VPP support</span></div><span><Value datum={e.net_interruptible_mw} /> <small>MW</small></span></div>
      <div className="flow-connector"><ArrowDownRight size={14} /><span>Modeled exposure × compute density</span></div>
      <div className="economics-row loss-row"><div><span className="economics-label">Modeled interruption cost</span><span className="economics-detail">Annual equivalent</span></div><span className="text-amber"><Value datum={e.annual_loss_usd} format={money} /> <small>/yr</small></span></div>
    </div>
    <div className="economics-quantiles"><table><caption>ANNUAL COST SCENARIOS · USD / YEAR <MockLabel sources={[economicsSource]} /></caption><thead><tr>{([50, 90, 99] as const).map(percentile => <th key={percentile}><Percentile value={percentile} /></th>)}</tr></thead><tbody><tr>{(['p50', 'p90', 'p99'] as const).map(key => <td key={key}><Value datum={e.annual_loss_by_quantile[key]} format={money} /></td>)}</tr></tbody></table></div>
    <div className="term-ledger">
      <div className="ledger-heading">OVER YOUR <Sourced value={inputs.contract_years} source={sourceFor('contract_years')} format={integer.format} />-YEAR TERM</div>
      <div><span>Earlier-access contribution</span><Value datum={e.early_access_value_usd} format={signedMoney} className="text-mint" /></div>
      <div><span>Modeled interruption cost</span><Value datum={{ ...e.term_loss_usd, value: -e.term_loss_usd.value, ref: `${e.term_loss_usd.ref}; display_as_ledger_debit = -term_loss_usd` }} format={money} /></div>
      <div className="ledger-net"><span>Net value vs. waiting <MockLabel sources={[e.net_value_usd]} /></span><Value datum={e.net_value_usd} format={signedMoney} /></div>
    </div>
    <div className="decision-readout" role="status" aria-live="polite" aria-atomic="true">
      <div className="decision-icon">{state === 'positive' ? <ArrowUpRight size={22} /> : state === 'negative' ? <ArrowDownRight size={22} /> : <ArrowRight size={22} />}</div>
      <div><span className="eyebrow">UNDER THESE ASSUMPTIONS <MockLabel sources={[economicsSource]} children="Mock decision" /></span><h3 ref={decisionRef}>{decision}</h3><p>{state === 'positive' ? 'Earlier-access contribution exceeds the upper-tail term cost.' : state === 'negative' ? 'Median term cost exceeds earlier-access contribution.' : 'The quantiles straddle the trade-off or sit near break-even.'}</p></div>
    </div>
    <div className="break-even-row"><span>Break-even modeled exposure</span><strong>{e.break_even_exposure_hours.value === null ? 'No modeled cost' : <><Value datum={e.break_even_exposure_hours as SourcedValue} /> <small>h/yr</small></>}</strong></div>
  </section>;
}

function Assumptions() {
  const { inputs, result, sourceFor } = useScenario();
  return <section className="assumptions-panel" aria-labelledby="assumptions-title">
    <div className="assumptions-heading"><div className="flex items-center gap-2"><SlidersHorizontal size={14} /><h2 id="assumptions-title">Economic assumptions</h2></div><span className="eyebrow muted">EDITABLE INPUTS</span></div>
    <div className="assumption-fields">
      <NumberField compact name="firm_wait_years" label="EARLIER ACCESS" unit="years" min={0} max={20} step={0.25} />
      <NumberField compact name="gpu_per_mw" label="COMPUTE DENSITY" unit="GPU / MW" min={1} max={2000} />
      <NumberField compact name="gpu_hour_value_usd" label="LOST COMPUTE VALUE" unit="$ / GPU-h" min={0} max={100} step={0.1} />
      <NumberField compact name="early_margin_usd_per_mw_year" label="EARLY OPERATING MARGIN" unit="$ / MW / yr" min={0} max={10000000} step={1000} />
    </div>
    <div className="assumption-notes"><p><span className="note-label">VALUE OF TIME</span>Earlier contribution = total load × operating margin × earlier-access years, capped at your contract term. Losses apply to the interruptible share across the full term. The ledger follows the median path; percentile paths are comparisons, not percentiles of total contract loss.</p><p><span className="note-label">DECISION RULE</span>Compare full-term costs with earlier-access contribution, using a <Sourced value={result.decision_policy.close_call_fraction.value * 100} source={{ ...result.decision_policy.close_call_fraction, ref: `${result.decision_policy.close_call_fraction.ref}; display_percent = fraction * 100` }} format={integer.format} />% margin: “not worth it” when <Percentile value={50} /> cost exceeds it; “worth it” when <Percentile value={90} /> cost stays below it; “close call” otherwise. This scenario excludes discounting, restart overhead, and SLA penalties.</p></div>
    {inputs.firm_wait_years > inputs.contract_years && <p className="assumption-notice">Earlier-access contribution is capped at <Sourced value={inputs.contract_years} source={sourceFor('contract_years')} /> years for this contract.</p>}
    <div className="honesty-note"><CircleHelp size={15} /><p><strong>A scenario, not a site forecast.</strong> Public grid data can establish system stress; local transmission headroom determines whether a specific site would be curtailed. A flexible share does not establish eligibility for a particular tariff.</p></div>
  </section>;
}

function Workspace() {
  const { sensitivity } = useScenario();
  return <div className="app-shell"><a className="skip-link" href="#main">Skip to analysis</a><Header /><main id="main"><Inputs /><ExposureControl /><div className="results-grid"><ExposurePanel /><EconomicsPanel /></div><SensitivityPanel sensitivity={sensitivity} /><Assumptions /><ScenarioComparison /><ZoneLeaderboard /></main><footer><span className="flex items-center gap-2"><Unplug size={12} />NO LIVE GRID FETCHES</span><span>Every number has a source. Hover, focus, or click a value or source tag.</span></footer></div>;
}

export default function App() { return <ScenarioProvider><Workspace /></ScenarioProvider>; }
