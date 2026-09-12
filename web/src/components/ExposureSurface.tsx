import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { ChevronLeft, ChevronRight, Minus, Plus, RotateCcw, RotateCw } from 'lucide-react';
import type { BaselineYear, Source } from '../model';
import { Sourced, SourceInfo } from './Sourced';
import { ConfidenceBadge, type ConfidenceEstimate } from './ConfidenceBadge';
import { QUANTILES, type SurfaceQuantile } from './exposure-surface/geometry';
import { createSurfaceRenderer, type AxisLabel, type SurfaceController } from './exposure-surface/renderer';

type Props = { rows: BaselineYear[]; maximumHours: number; confidence?: ConfidenceEstimate; onUnavailable: () => void };
const quantileSource = (value: number): Source => ({ source_type: 'assumption', ref: `mock://display/surface/percentile/${value}; cumulative percentile, not probability density` });
const hours = (value: number) => value.toLocaleString('en-US', { maximumFractionDigits: 1 });

export default function ExposureSurface({ rows, maximumHours, confidence, onUnavailable }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<SurfaceController | null>(null);
  const unavailable = useRef(onUnavailable);
  unavailable.current = onUnavailable;
  const [labels, setLabels] = useState<AxisLabel[]>([]);
  const [yearIndex, setYearIndex] = useState(0);
  const [quantile, setQuantile] = useState<SurfaceQuantile>(90);
  const currentYearIndex = Math.min(yearIndex, rows.length - 1);
  const row = rows[currentYearIndex];
  const datum = row[`p${quantile}`];

  useEffect(() => {
    if (!host.current || typeof WebGL2RenderingContext === 'undefined') { unavailable.current(); return; }
    try {
      controller.current = createSurfaceRenderer(host.current, {
        labels: setLabels,
        select: index => { setYearIndex(Math.floor(index / QUANTILES.length)); setQuantile(QUANTILES[index % QUANTILES.length]); },
        unavailable: () => unavailable.current(),
      });
    } catch { unavailable.current(); }
    return () => { controller.current?.dispose(); controller.current = null; };
  }, []);

  useEffect(() => {
    try { controller.current?.update(rows, maximumHours); } catch { unavailable.current(); }
  }, [rows, maximumHours]);
  useEffect(() => { controller.current?.select(currentYearIndex * QUANTILES.length + QUANTILES.indexOf(quantile)); }, [currentYearIndex, quantile, rows]);

  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    const scene = controller.current;
    if (!scene) return;
    if (event.key === 'ArrowLeft') scene.rotate(-0.12);
    else if (event.key === 'ArrowRight') scene.rotate(0.12);
    else if (event.key === 'ArrowUp') scene.rotate(0, -0.1);
    else if (event.key === 'ArrowDown') scene.rotate(0, 0.1);
    else if (event.key === '+' || event.key === '=') scene.zoom(1.1);
    else if (event.key === '-') scene.zoom(1 / 1.1);
    else if (event.key === 'Home') scene.reset();
    else return;
    event.preventDefault();
  }

  return <div className="surface-view">
    <div className="surface-toolbar"><span>Drag to rotate · inspect nearest supplied point</span><div className="surface-camera-controls" role="group" aria-label="Surface camera">
      <button aria-label="Rotate surface left" title="Rotate left" onClick={() => controller.current?.rotate(-0.15)}><RotateCcw size={13} /></button>
      <button aria-label="Rotate surface right" title="Rotate right" onClick={() => controller.current?.rotate(0.15)}><RotateCw size={13} /></button>
      <button aria-label="Zoom out" title="Zoom out" onClick={() => controller.current?.zoom(1 / 1.1)}><Minus size={13} /></button>
      <button aria-label="Zoom in" title="Zoom in" onClick={() => controller.current?.zoom(1.1)}><Plus size={13} /></button>
      <button className="surface-reset" onClick={() => controller.current?.reset()}>Reset view</button>
    </div></div>
    <div className="surface-axis-key"><span>BASE: YEAR × PERCENTILE</span><span>HEIGHT: MODELED EXPOSURE · H/YR</span></div>
    <div className="surface-stage" role="group" tabIndex={0} aria-label="Interactive modeled exposure quantile surface" aria-describedby="surface-keyboard-help" onKeyDown={keyboard}>
      <div className="surface-canvas" ref={host} />
      <div className="surface-axis-labels"><svg className="surface-label-leaders" width="100%" height="100%" aria-hidden="true">{labels.map(label => <line key={label.id} x1={label.anchorX} y1={label.anchorY} x2={label.x} y2={label.y} stroke="var(--border-strong)" strokeWidth="0.7" />)}</svg>{labels.map(label => <div key={label.id} className={`surface-axis-label axis-${label.kind}`} style={{ left: label.x, top: label.y }}>
        <Sourced value={label.datum.value} source={label.datum} animate={false} format={value => label.kind === 'percentile' ? `p${value}` : label.kind === 'year' ? `Y${value}` : `${value.toLocaleString('en-US')} h`} />
      </div>)}</div>
    </div>
    <p id="surface-keyboard-help" className="sr-only">Arrow keys rotate the surface. Plus and minus zoom. Home resets the camera. Use the year and percentile inspector below or the annual data table for exact sourced values.</p>
    <div className="surface-inspector" aria-label="Selected supplied quantile">
      <div className="surface-inspector-year"><span className="eyebrow">YEAR</span><button onClick={() => setYearIndex(Math.max(0, currentYearIndex - 1))} disabled={currentYearIndex === 0} aria-label="Inspect previous year"><ChevronLeft size={13} /></button><Sourced value={row.year.value} source={row.year} animate={false} /><button onClick={() => setYearIndex(Math.min(rows.length - 1, currentYearIndex + 1))} disabled={currentYearIndex === rows.length - 1} aria-label="Inspect next year"><ChevronRight size={13} /></button></div>
      <div className="surface-quantile-controls" role="group" aria-label="Inspect percentile">{QUANTILES.map(value => <span key={value} className={quantile === value ? 'selected-quantile' : ''}><button onClick={() => setQuantile(value)} aria-pressed={quantile === value} aria-label={`Inspect p${value}`} title={JSON.stringify({ value, ...quantileSource(value) })}>p{value}</button><SourceInfo value={value} source={quantileSource(value)} label={`p${value} percentile definition`} /></span>)}</div>
      <div className="surface-inspected-value"><Sourced value={quantile} source={quantileSource(quantile)} animate={false}>p{quantile}</Sourced><span>modeled exposure</span><strong><Sourced value={datum.value} source={datum} format={hours} /></strong><span>h/yr</span>{confidence && <ConfidenceBadge confidence={confidence} compact />}</div>
    </div>
    <p className="surface-explanation">{rows.length === 1 ? 'A single-year quantile cross-section; no time surface is implied. ' : 'Faces connect supplied annual quantiles; intermediate positions are visual interpolation. '}Percentile spacing is proportional. This is not a probability-density estimate. Readouts are rounded; source tags retain exact values.</p>
  </div>;
}
