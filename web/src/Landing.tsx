import { useMemo, useState } from 'react';
import { ArrowDownRight, ArrowRight, ArrowUpRight } from 'lucide-react';
import { deriveScenario, defaultInputs } from './model';
import { useAnimatedNumber } from './hooks/useAnimatedNumber';
import './landing.css';

const numeric = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const money = (value: number) => `${value < 0 ? '−' : ''}$${(Math.abs(value) / 1_000_000).toFixed(2)}M`;

function BrandMark() {
  return <span className="brand-mark" aria-hidden="true"><img className="brand-image" src="/fluxline-mark.svg" alt="" width="48" height="48" /></span>;
}

function HeroDemo() {
  const [exposure, setExposure] = useState(0.4);
  const result = useMemo(() => deriveScenario({ ...defaultInputs, site_exposure: exposure }), [exposure]);
  const p50 = useAnimatedNumber(result.annual_exposure.p50.value);
  const cost = useAnimatedNumber(result.economics.annual_loss_usd.value);
  const state = result.decision === 'worth it' ? 'positive' : result.decision === 'not worth it' ? 'negative' : 'neutral';
  return (
    <div className="hero-demo" aria-label="Interactive exposure preview">
      <div className="hero-demo-label">
        <div className="hero-demo-heading">
          <span className="eyebrow">LIVE SCENARIO INPUT</span>
          <strong>Site exposure factor</strong>
        </div>
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
        <span><span className="num">0.0</span> No exposure</span>
        <span>Full modeled exposure <span className="num">1.0</span></span>
      </div>
      <div className="hero-demo-row">
        <span>Share of system stress mapped to your site</span>
        <div className={`hero-demo-stats decision-${state}`}>
          <div><span className="eyebrow">MODELED EXPOSURE, <span className="num">p50</span></span><strong>{numeric.format(p50)} h/yr</strong></div>
          <div><span className="eyebrow">MODELED COST</span><strong>{money(cost)}/yr</strong></div>
          <div className="hero-demo-decision">
            {state === 'positive' ? <ArrowUpRight size={16} /> : state === 'negative' ? <ArrowDownRight size={16} /> : <ArrowRight size={16} />}
            <strong>{result.decision}</strong>
          </div>
        </div>
      </div>
      <p className="hero-demo-note">Assumed SPP scenario, <span className="num">100</span> MW load, <span className="num">60%</span> flexible, <span className="num">7</span>-year term. Drag the slider. These are scenario assumptions, not a forecast for a specific site.</p>
    </div>
  );
}

export function Landing() {
  return <div id="top" className="landing landing-redesign">
    <a href="#main" className="skip-link">Skip to the problem</a>
    <header className="app-header land-header reveal">
      <a href="/" className="brand" aria-label="fluxline home"><BrandMark />fluxline<span className="brand-divider" /><span className="brand-subtitle">INTERCONNECTION RISK</span></a>
      <a className="button" href="/app">Run your scenario<ArrowRight size={14} /></a>
    </header>

    <section className="hero">
      <div className="hero-copy">
        <span className="eyebrow land-eyebrow">SPP · CHILLS / LIVE JULY <span className="num">1, 2026</span></span>
        <h1>The contract won&rsquo;t tell you<br />when they&rsquo;ll cut your power.</h1>
        <p className="hero-lede">So we learned it from what they actually do.</p>
        <p className="hero-sub">Enter a location, a load size, and how much of your compute can pause. Get a modeled exposure range, a cost, and a break-even point, with every number traced to a dataset, a filed tariff clause, or an assumption you set yourself.</p>
        <div className="hero-actions">
          <a className="button button-primary" href="/app">Run your scenario<ArrowRight size={14} /></a>
          <a className="text-button" href="#main">See how it&rsquo;s priced &darr;</a>
        </div>
      </div>
      <HeroDemo />
    </section>

    <main id="main">
      <section className="land-section land-problem reveal">
        <div>
          <span className="eyebrow land-eyebrow">THE PROBLEM</span>
          <h2>A faster grid connection with an unpriced catch</h2>
          <p>Grid operators are starting to offer large loads a trade: connect years sooner, in exchange for accepting that the operator can cut your power whenever the system is strained. SPP calls its version CHILLS. It runs up to seven years, and shares the same curtailment priority as ordinary non-firm transmission service, a decades-old, uncapped framework never built for a <span className="num">$100M</span>, multi-year AI data center commitment.</p>
          <p>In June <span className="num">2026</span>, FERC ordered all six U.S. grid operators to justify or reform their large-load interconnection rules. The ambiguity isn&rsquo;t a gap regulators are racing to close: one intervenor asked FERC to require SPP to define its curtailment triggers and procedures. FERC found the existing language sufficient.</p>
        </div>
        <dl className="land-stat-ledger">
          <div><dt>7 YRS</dt><dd>Maximum CHILLS term<small>FERC order <span className="num">195</span> FERC &para;<span className="num">61,196</span></small></dd></div>
          <div><dt>0</dt><dd>Stated cap on curtailment hours<small>Same order, &para;&para;<span className="num">30&ndash;34</span></small></dd></div>
          <div><dt>6</dt><dd>RTOs ordered by FERC to respond<small>June <span className="num">18, 2026</span> show-cause orders</small></dd></div>
        </dl>
      </section>

      <section className="land-section land-layers">
        <span className="eyebrow land-eyebrow">HOW IT WORKS</span>
        <h2>Three layers, one honest number</h2>
        <ol className="land-layer-list">
          <li className="reveal"><span className="land-layer-tag">LAYER <span className="num">01</span></span><div><h3>Exposure model</h3><p>An ensemble trained on years of public SPP grid data (load, reserves, binding transmission constraints) learns when the system was actually under stress. Not a guess from the contract&rsquo;s vague language.</p></div></li>
          <li className="reveal"><span className="land-layer-tag">LAYER <span className="num">02</span></span><div><h3>Tariff extraction</h3><p>A second model reads the FERC-filed tariff text itself and pulls out the curtailment triggers it can find, with a citation back to the filing for each one.</p></div></li>
          <li className="reveal"><span className="land-layer-tag">LAYER <span className="num">03</span></span><div><h3>Confidence</h3><p>A third layer measures how much the model&rsquo;s members agree with each other, and how much historical precedent exists for a grid state like this one. Every exposure number carries a High, Medium, or Low read on how far to trust it.</p></div></li>
        </ol>
      </section>

      <section className="land-section land-why reveal">
        <span className="eyebrow land-eyebrow">WHY IT MATTERS</span>
        <p className="land-why-lede">There are two ways to connect the new demand from AI data centers: build a new power plant, usually gas, or make the load flexible enough to back off when the grid is strained. Flexibility is the option that avoids the plant. Almost nobody takes it, because the risk of it has never been priced.</p>
        <p className="land-why-punch">The thing blocking the cleaner path is an unpriced risk.</p>
        <p className="land-why-punch land-why-payoff">We price it.</p>
      </section>
    </main>

    <footer className="land-footer reveal">
      <div className="land-footer-brand" aria-label="Fluxline interconnection risk">
        <BrandMark />
        <span className="land-footer-wordmark">fluxline</span>
        <span className="land-footer-divider" aria-hidden="true" />
        <span className="land-footer-subtitle">INTERCONNECTION RISK</span>
      </div>
      <a className="button button-primary" href="/app">Run your scenario<ArrowRight size={14} /></a>
      <a className="land-footer-top" href="#top">Back to top<ArrowUpRight size={14} /></a>
    </footer>
  </div>;
}
