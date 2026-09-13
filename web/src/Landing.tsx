import { useMemo, useState } from 'react';
import { ArrowDownRight, ArrowRight, ArrowUpRight } from 'lucide-react';
import { deriveScenario, defaultInputs } from './model';
import { useAnimatedNumber } from './hooks/useAnimatedNumber';
import './landing.css';

const numeric = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const money = (value: number) => `${value < 0 ? '−' : ''}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`;

function BrandMark() {
  return <span className="brand-mark" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 18 18" fill="none"><path d="M3 2V16M15 2V16M3 9H15M7 5V13M11 5V13" stroke="currentColor" strokeWidth="1.3" /></svg></span>;
}

function HeroDemo() {
  const [exposure, setExposure] = useState(0.4);
  const result = useMemo(() => deriveScenario({ ...defaultInputs, site_exposure: exposure }), [exposure]);
  const p50 = useAnimatedNumber(result.annual_exposure.p50.value);
  const cost = useAnimatedNumber(result.economics.annual_loss_usd.value);
  const state = result.decision === 'worth it' ? 'positive' : result.decision === 'not worth it' ? 'negative' : 'neutral';
  return (
    <div className="hero-demo">
      <div className="hero-demo-label">
        <span className="eyebrow">TRY IT — SAME MATH AS THE FULL TOOL</span>
        <span className="hero-demo-value">{exposure.toFixed(2)}</span>
      </div>
      <div className="slider-track-wrap">
        <span className="slider-progress" style={{ width: `${exposure * 100}%` }} />
        <input
          type="range" min={0} max={1} step={0.01} value={exposure}
          aria-label="Share of system stress mapped to your site"
          onChange={event => setExposure(Number(event.target.value))}
        />
      </div>
      <div className="slider-endpoints">
        <span>0.0 No exposure</span>
        <span>Full modeled exposure 1.0</span>
      </div>
      <div className="hero-demo-row">
        <span>Share of system stress mapped to your site</span>
        <div className={`hero-demo-stats decision-${state}`}>
          <div><span className="eyebrow">MODELED EXPOSURE, p50</span><strong>{numeric.format(p50)} h/yr</strong></div>
          <div><span className="eyebrow">MODELED COST</span><strong>{money(cost)}/yr</strong></div>
          <div className="hero-demo-decision">
            {state === 'positive' ? <ArrowUpRight size={16} /> : state === 'negative' ? <ArrowDownRight size={16} /> : <ArrowRight size={16} />}
            <strong>{result.decision}</strong>
          </div>
        </div>
      </div>
      <p className="hero-demo-note">Illustrative SPP node, 100 MW load, 60% flexible, 7-year term. Drag the slider — this is a preview, not a forecast for a specific site.</p>
    </div>
  );
}

export function Landing() {
  return <div className="landing">
    <a href="#main" className="skip-link">Skip to the problem</a>
    <header className="app-header land-header">
      <a href="/" className="brand" aria-label="Fluxline home"><BrandMark />Fluxline<span className="brand-divider" /><span className="brand-subtitle">INTERCONNECTION RISK</span></a>
      <a className="button" href="/app">Run your scenario<ArrowRight size={14} /></a>
    </header>

    <section className="hero">
      <span className="eyebrow land-eyebrow land-eyebrow-accent">SPP · CHILLS — LIVE JULY 1, 2026</span>
      <h1>The contract won&rsquo;t tell you<br />when they&rsquo;ll cut your power.</h1>
      <p className="hero-lede">We reconstruct exposure from actual historical grid operations.</p>
      <p className="hero-sub">Enter a location, a load size, and how much of your compute can pause. Get a modeled exposure range, a cost, and a break-even point &mdash; every number traced to a dataset, a filed tariff clause, or an assumption you set yourself.</p>
      <div className="hero-actions">
        <a className="button button-primary" href="/app">Run your scenario<ArrowRight size={14} /></a>
        <a className="text-button" href="#main">See how it&rsquo;s priced &darr;</a>
      </div>
    </section>

    <HeroDemo />

    <main id="main">
      <section className="land-section land-problem">
        <div>
          <span className="eyebrow land-eyebrow">THE PROBLEM</span>
          <h2>A faster grid connection with an unpriced catch</h2>
          <p>Grid operators are starting to offer large loads a trade: connect years sooner, in exchange for accepting that the operator can cut your power whenever the system is strained. SPP calls its version CHILLS. It runs up to seven years, and shares the same curtailment priority as ordinary non-firm transmission service &mdash; a decades-old, uncapped framework never built for a $100M, multi-year AI data center commitment.</p>
          <p>In June 2026, FERC ordered all six U.S. grid operators to justify or reform their large-load interconnection rules. The ambiguity isn&rsquo;t a gap regulators are racing to close: one intervenor asked FERC to require SPP to define its curtailment triggers and procedures. FERC found the existing language sufficient.</p>
        </div>
        <dl className="land-stat-ledger">
          <div><dt>7 YRS</dt><dd>Maximum CHILLS term<small>FERC order 195 FERC &para;61,196</small></dd></div>
          <div><dt>0</dt><dd>Stated cap on curtailment hours<small>Same order, &para;&para;30&ndash;34</small></dd></div>
          <div><dt>6</dt><dd>RTOs ordered by FERC to respond<small>June 18, 2026 show-cause orders</small></dd></div>
        </dl>
      </section>

      <section className="land-section land-layers">
        <span className="eyebrow land-eyebrow">HOW IT WORKS</span>
        <h2>Three layers, one honest number</h2>
        <ol className="land-layer-list">
          <li><span className="land-layer-tag">LAYER 01</span><div><h3>Exposure model</h3><p>An ensemble of machine-learning models trained on years of public SPP operational telemetry&mdash;including aggregate load, regional reserve margins, and binding transmission constraints&mdash;to reconstruct historical periods of system-wide stress.</p></div></li>
          <li><span className="land-layer-tag">LAYER 02</span><div><h3>Tariff extraction</h3><p>A structured parsing pipeline that extracts explicit curtailment triggers from FERC-filed tariff sheets, mapping each trigger to its official filing section and page citation.</p></div></li>
          <li><span className="land-layer-tag">LAYER 03</span><div><h3>Confidence calibration</h3><p>A calibration layer that evaluates model ensemble variance and historical data density to compute a structured confidence score (High, Medium, Low), indicating the statistical support behind each risk scenario.</p></div></li>
        </ol>
      </section>

      <section className="land-section land-why">
        <span className="eyebrow land-eyebrow">WHY IT MATTERS</span>
        <p className="land-why-lede">There are two ways to connect the new demand from AI data centers: build a new power plant, usually gas &mdash; or make the load flexible enough to back off when the grid is strained. Flexibility is the option that avoids the plant. Almost nobody takes it, because the risk of it has never been priced.</p>
        <p className="land-why-punch">The thing blocking the cleaner path is an unpriced risk.</p>
        <p className="land-why-punch land-why-payoff">We price it.</p>
      </section>
    </main>

    <footer className="app-shell land-footer">
      <span>OFFLINE-READY FIXTURE · NO LIVE GRID DATA</span>
      <a className="button button-primary" href="/app">Run your scenario<ArrowRight size={14} /></a>
    </footer>
  </div>;
}
