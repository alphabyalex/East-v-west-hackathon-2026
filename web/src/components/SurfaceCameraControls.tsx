import { Minus, Plus, RotateCcw, RotateCw } from 'lucide-react'
import type { SurfaceController } from './exposure-surface/renderer'

export function SurfaceCameraControls({ controller }: { controller: () => SurfaceController | null }) {
  return <div className="surface-camera-controls" role="group" aria-label="Surface camera">
    <button aria-label="Rotate surface left" title="Rotate left" onClick={() => controller()?.rotate(-0.15)}><RotateCcw size={13} /></button>
    <button aria-label="Rotate surface right" title="Rotate right" onClick={() => controller()?.rotate(0.15)}><RotateCw size={13} /></button>
    <button aria-label="Zoom out" title="Zoom out" onClick={() => controller()?.zoom(1 / 1.1)}><Minus size={13} /></button>
    <button aria-label="Zoom in" title="Zoom in" onClick={() => controller()?.zoom(1.1)}><Plus size={13} /></button>
    <button className="surface-reset" onClick={() => controller()?.reset()}>Reset view</button>
  </div>
}
