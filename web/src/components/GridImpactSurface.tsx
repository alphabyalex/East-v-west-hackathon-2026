import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { Sourced } from './Sourced'
import { SurfaceCameraControls } from './SurfaceCameraControls'
import { createSurfaceRenderer, type AxisLabel, type SurfaceController } from './exposure-surface/renderer'
import type { powerScenario } from '../api/grid-impact'

type Bin = ReturnType<typeof powerScenario>['bins'][number]
const monthName = (value: number) => new Date(Date.UTC(2025, value - 1)).toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
const dollars = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })

export default function GridImpactSurface({ bins, onUnavailable }: { bins: Bin[]; onUnavailable: () => void }) {
  const host = useRef<HTMLDivElement>(null)
  const controller = useRef<SurfaceController | null>(null)
  const unavailable = useRef(onUnavailable)
  unavailable.current = onUnavailable
  const [labels, setLabels] = useState<AxisLabel[]>([])
  const [selected, setSelected] = useState(0)
  const grid = useMemo(() => ({ rows: bins.filter((_, i) => i % 24 === 0).map(bin => bin.month),
    columns: bins.slice(0, 24).map(bin => bin.hour_utc),
    values: Array.from({ length: 12 }, (_, month) => bins.slice(month * 24, month * 24 + 24).map(bin => bin.dollars)) }), [bins])
  const maximum = Math.max(1, ...bins.map(bin => bin.dollars.value))
  useEffect(() => {
    if (!host.current || typeof WebGL2RenderingContext === 'undefined') { unavailable.current(); return }
    try { controller.current = createSurfaceRenderer(host.current, { labels: setLabels, select: setSelected, unavailable: () => unavailable.current() }) }
    catch { unavailable.current() }
    return () => { controller.current?.dispose(); controller.current = null }
  }, [])
  useEffect(() => { try { controller.current?.updateGrid(grid, maximum) } catch { unavailable.current() } }, [grid, maximum])
  useEffect(() => { controller.current?.select(selected) }, [selected])
  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return
    if (event.key === 'ArrowLeft') controller.current?.rotate(-.12)
    else if (event.key === 'ArrowRight') controller.current?.rotate(.12)
    else if (event.key === 'ArrowUp') controller.current?.rotate(0, -.1)
    else if (event.key === 'ArrowDown') controller.current?.rotate(0, .1)
    else if (['+', '='].includes(event.key)) controller.current?.zoom(1.1)
    else if (event.key === '-') controller.current?.zoom(1 / 1.1)
    else if (event.key === 'Home') controller.current?.reset()
    else return
    event.preventDefault()
  }
  const bin = bins[selected]
  return <div className="surface-view">
    <div className="surface-toolbar"><span>Drag to rotate · inspect nearest supplied point</span><SurfaceCameraControls controller={() => controller.current} /></div>
    <div className="surface-axis-key"><span>BASE: MONTH × UTC HOUR</span><span>HEIGHT: WHOLESALE SCENARIO VALUE · USD</span></div>
    <div className="surface-stage" role="group" tabIndex={0} aria-label="Interactive grid-impact dollar surface" onKeyDown={keyboard}>
      <div className="surface-canvas" ref={host} />
      <div className="surface-axis-labels">{labels.map(label => <div key={label.id} className={`surface-axis-label axis-${label.kind}`} style={{ left: label.x, top: label.y }}>
        <Sourced value={label.datum.value} source={label.datum} animate={false} format={value => label.kind === 'year' ? monthName(value) : label.kind === 'percentile' ? `${String(value).padStart(2, '0')}:00` : dollars(value)} />
      </div>)}</div>
    </div>
    <div className="surface-inspector grid-power-inspector">
      <label>Inspect month<select aria-label="Inspect grid-impact month" value={Math.floor(selected / 24)} onChange={event => setSelected(Number(event.target.value) * 24 + selected % 24)}>{grid.rows.map((row, i) => <option key={i} value={i} data-provenance={JSON.stringify(row)}>{monthName(row.value)}</option>)}</select></label>
      <label>UTC hour<select aria-label="Inspect grid-impact UTC hour" value={selected % 24} onChange={event => setSelected(Math.floor(selected / 24) * 24 + Number(event.target.value))}>{grid.columns.map((column, i) => <option key={i} value={i} data-provenance={JSON.stringify(column)}>{String(i).padStart(2, '0')}:00</option>)}</select></label>
      <span><Sourced value={bin.proxy_hours.value} source={bin.proxy_hours} /> qualifying hours</span>
      <span><Sourced value={bin.wind.value} source={bin.wind} /> MWh</span>
      <strong><Sourced value={bin.dollars.value} source={bin.dollars} format={dollars} /></strong>
      <span><Sourced value={bin.unknown_hours.value} source={bin.unknown_hours} /> unknown hours</span>
    </div>
    <p className="surface-explanation">Each point sums observed qualifying hours at one month and UTC hour. Faces interpolate visually between supplied bins; they are not forecasts. Unknown hours are excluded from each observed subtotal. Arrow keys rotate; plus/minus zoom; Home resets.</p>
  </div>
}
