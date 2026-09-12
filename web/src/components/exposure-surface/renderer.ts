import {
  AmbientLight, BufferAttribute, BufferGeometry, DirectionalLight, DoubleSide,
  Group, Line, LineBasicMaterial, LineSegments, Mesh, MeshLambertMaterial,
  OrthographicCamera, Points, PointsMaterial, Raycaster, Scene, Spherical,
  Vector2, Vector3, WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { BaselineYear, SourcedValue } from '../../model';
import { buildSurfaceData, QUANTILES, SURFACE_SIZE, type SurfaceSample } from './geometry';

export type AxisLabel = {
  id: string; kind: 'year' | 'percentile' | 'hours'; datum: SourcedValue;
  x: number; y: number; anchorX: number; anchorY: number;
};
export type SurfaceController = ReturnType<typeof createSurfaceRenderer>;
type Callbacks = { labels: (labels: AxisLabel[]) => void; select: (index: number) => void; unavailable: () => void };
const source = (value: number, ref: string): SourcedValue => ({ value, source_type: 'assumption', ref: `mock://display/surface/${ref}` });

/** A small, demand-rendered scene: only a camera interaction or data transition draws frames. */
export function createSurfaceRenderer(host: HTMLDivElement, callbacks: Callbacks) {
  const renderer = new WebGLRenderer({ antialias: true, powerPreference: 'low-power' });
  renderer.setClearColor('#14161a');
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.domElement.setAttribute('aria-hidden', 'true');
  host.appendChild(renderer.domElement);

  const scene = new Scene();
  const camera = new OrthographicCamera(-9, 9, 5.5, -5.5, 0.1, 100);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 1.7, 0);
  controls.enableDamping = false;
  controls.enablePan = false;
  controls.autoRotate = false;
  controls.minPolarAngle = 0.4;
  controls.maxPolarAngle = 1.38;
  controls.minAzimuthAngle = -0.85;
  controls.maxAzimuthAngle = 1.3;
  controls.minZoom = 0.7;
  controls.maxZoom = 1.65;
  controls.rotateSpeed = 0.55;
  controls.zoomSpeed = 0.65;
  scene.add(new AmbientLight('#ffffff', 1.5));
  const light = new DirectionalLight('#ffffff', 1.6);
  light.position.set(-6, 12, 8);
  scene.add(light);

  const dataGroup = new Group();
  const gridGroup = new Group();
  scene.add(dataGroup, gridGroup);
  const raycaster = new Raycaster();
  raycaster.params.Points = { threshold: 0.14 };
  const pointer = new Vector2();
  let samples: SurfaceSample[] = [];
  let positions = new Float32Array();
  let mesh: Mesh<BufferGeometry, MeshLambertMaterial> | undefined;
  let points: Points<BufferGeometry, PointsMaterial> | undefined;
  let selectedPoint: Points<BufferGeometry, PointsMaterial> | undefined;
  let selectionGuide: Line<BufferGeometry, LineBasicMaterial> | undefined;
  let quantileLines: Line<BufferGeometry, LineBasicMaterial>[] = [];
  let yearLines: Line<BufferGeometry, LineBasicMaterial>[] = [];
  let axisLabels: { id: string; kind: AxisLabel['kind']; datum: SourcedValue; point: Vector3 }[] = [];
  let width = 1;
  let height = 1;
  let selected = 0;
  let frame: number | undefined;
  let disposed = false;
  let initialized = false;
  let transition: { from: Float32Array; to: Float32Array; started: number } | undefined;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  function clearGroup(group: Group) {
    group.traverse(object => {
      if (object instanceof Mesh || object instanceof Line || object instanceof Points) {
        object.geometry.dispose();
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach(material => material.dispose());
      }
    });
    group.clear();
  }

  function projected(point: Vector3) {
    const projected = point.clone().project(camera);
    return { x: (projected.x + 1) * width / 2, y: (1 - projected.y) * height / 2, z: projected.z };
  }

  function drawLabels() {
    const labels: AxisLabel[] = axisLabels.flatMap(label => {
      const position = projected(label.point);
      if (position.z < -1 || position.z > 1 || position.x < 0 || position.x > width || position.y < 0 || position.y > height) return [];
      return [{ ...label, anchorX: position.x, anchorY: position.y,
        x: Math.max(25, Math.min(width - 30, position.x + (label.kind === 'hours' ? -18 : label.kind === 'percentile' ? 21 : 0))),
        y: Math.max(10, Math.min(height - 16, position.y + (label.kind === 'year' ? 17 : 0))),
      }];
    });
    // The upper percentiles are correctly close on the axis. Leaders keep their
    // labels legible without moving the data coordinates or inventing equal spacing.
    const quantiles = labels.filter(label => label.kind === 'percentile').sort((a, b) => a.y - b.y);
    for (let i = 1; i < quantiles.length; i++) {
      if (Math.abs(quantiles[i].x - quantiles[i - 1].x) < 55 && quantiles[i].y - quantiles[i - 1].y < 22) {
        quantiles[i].y = Math.min(height - 16, quantiles[i - 1].y + 22);
      }
    }
    callbacks.labels(labels);
  }

  function render() {
    if (disposed) return;
    renderer.render(scene, camera);
    drawLabels();
  }

  function syncSelection() {
    if (!selectedPoint || !selectionGuide || !samples.length) return;
    const offset = Math.min(selected, samples.length - 1) * 3;
    const [x, y, z] = positions.subarray(offset, offset + 3);
    const marker = selectedPoint.geometry.getAttribute('position') as BufferAttribute;
    marker.setXYZ(0, x, y, z);
    marker.needsUpdate = true;
    selectedPoint.geometry.computeBoundingSphere();
    const guide = selectionGuide.geometry.getAttribute('position') as BufferAttribute;
    guide.setXYZ(0, x, 0.01, z);
    guide.setXYZ(1, x, y, z);
    guide.needsUpdate = true;
    selectionGuide.geometry.computeBoundingSphere();
  }

  function syncGeometry() {
    if (!mesh || !points) return;
    const attribute = mesh.geometry.getAttribute('position') as BufferAttribute;
    attribute.array.set(positions);
    attribute.needsUpdate = true;
    mesh.geometry.computeVertexNormals();
    mesh.geometry.computeBoundingSphere();
    const pointAttribute = points.geometry.getAttribute('position') as BufferAttribute;
    pointAttribute.array.set(positions);
    pointAttribute.needsUpdate = true;
    points.geometry.computeBoundingSphere();
    quantileLines.forEach((line, quantileIndex) => {
      const array = line.geometry.getAttribute('position') as BufferAttribute;
      for (let year = 0; year < yearLines.length; year++) {
        const offset = (year * QUANTILES.length + quantileIndex) * 3;
        array.setXYZ(year, positions[offset], positions[offset + 1], positions[offset + 2]);
      }
      array.needsUpdate = true;
      line.geometry.computeBoundingSphere();
    });
    yearLines.forEach((line, yearIndex) => {
      const array = line.geometry.getAttribute('position') as BufferAttribute;
      array.array.set(positions.subarray(yearIndex * QUANTILES.length * 3, (yearIndex + 1) * QUANTILES.length * 3));
      array.needsUpdate = true;
      line.geometry.computeBoundingSphere();
    });
    syncSelection();
  }

  function animate(time: number) {
    frame = undefined;
    if (!transition || disposed) return;
    const progress = reducedMotion.matches ? 1 : Math.min(1, (time - transition.started) / 280);
    const eased = 1 - (1 - progress) ** 3;
    for (let i = 0; i < positions.length; i++) positions[i] = transition.from[i] + (transition.to[i] - transition.from[i]) * eased;
    syncGeometry();
    render();
    if (progress < 1) frame = window.requestAnimationFrame(animate);
    else transition = undefined;
  }

  function line(points: number[], color: string, group: Group, segments = false) {
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(new Float32Array(points), 3));
    const material = new LineBasicMaterial({ color });
    const object = segments ? new LineSegments(geometry, material) : new Line(geometry, material);
    group.add(object);
    return object;
  }

  function update(rows: BaselineYear[], maximumHours: number) {
    const data = buildSurfaceData(rows, maximumHours);
    if (frame !== undefined) window.cancelAnimationFrame(frame);
    frame = undefined;
    const previous = positions;
    const canAnimate = initialized && previous.length === data.positions.length && !reducedMotion.matches;
    samples = data.samples;
    positions = canAnimate ? new Float32Array(previous) : new Float32Array(data.positions);
    clearGroup(dataGroup);
    clearGroup(gridGroup);
    quantileLines = [];
    yearLines = [];
    axisLabels = [];

    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
    geometry.setIndex(new BufferAttribute(data.indices, 1));
    geometry.computeVertexNormals();
    mesh = new Mesh(geometry, new MeshLambertMaterial({ color: '#5c8a86', side: DoubleSide, flatShading: true, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
    dataGroup.add(mesh);
    const pointGeometry = new BufferGeometry();
    pointGeometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
    points = new Points(pointGeometry, new PointsMaterial({ color: '#edebe6', size: 4, sizeAttenuation: false }));
    dataGroup.add(points);
    QUANTILES.forEach((quantile, quantileIndex) => {
      const vertices = rows.flatMap((__, yearIndex) => Array.from(positions.subarray((yearIndex * QUANTILES.length + quantileIndex) * 3, (yearIndex * QUANTILES.length + quantileIndex) * 3 + 3)));
      quantileLines.push(line(vertices, quantile === 50 || quantile === 99 ? '#edebe6' : '#5c8a86', dataGroup) as Line<BufferGeometry, LineBasicMaterial>);
    });
    rows.forEach((_, yearIndex) => {
      yearLines.push(line(Array.from(positions.subarray(yearIndex * QUANTILES.length * 3, (yearIndex + 1) * QUANTILES.length * 3)), '#858a91', dataGroup) as Line<BufferGeometry, LineBasicMaterial>);
    });
    const markerGeometry = new BufferGeometry();
    markerGeometry.setAttribute('position', new BufferAttribute(new Float32Array(3), 3));
    selectedPoint = new Points(markerGeometry, new PointsMaterial({ color: '#d98e2b', size: 8, sizeAttenuation: false, depthTest: false }));
    selectedPoint.renderOrder = 5;
    dataGroup.add(selectedPoint);
    selectionGuide = line([0, 0, 0, 0, 0, 0], '#d98e2b', dataGroup) as Line<BufferGeometry, LineBasicMaterial>;
    selectionGuide.renderOrder = 4;

    const { width: graphWidth, height: graphHeight, depth } = SURFACE_SIZE;
    const left = rows.length === 1 ? 0 : -graphWidth / 2;
    const right = rows.length === 1 ? 0 : graphWidth / 2;
    const front = depth / 2, back = -depth / 2;
    line([left, 0, front, right, 0, front, right, 0, back], '#858a91', gridGroup);
    line([left, 0, front, left, graphHeight, front], '#858a91', gridGroup);
    QUANTILES.forEach((quantile, index) => {
      const z = data.samples[index].position[2];
      line([left, 0, z, right, 0, z], '#262930', gridGroup);
      axisLabels.push({ id: `q-${quantile}`, kind: 'percentile', datum: source(quantile, `percentile/${quantile}; cumulative percentile, not probability density`), point: new Vector3(right + 0.15, 0, z) });
    });
    const stride = Math.max(1, Math.ceil((rows.length - 1) / 4));
    rows.forEach((row, index) => {
      const x = data.samples[index * QUANTILES.length].position[0];
      line([x, 0, front, x, 0, back], '#262930', gridGroup);
      if (index % stride === 0 || index === rows.length - 1) axisLabels.push({ id: `year-${row.year.value}`, kind: 'year', datum: row.year, point: new Vector3(x, 0, front + 0.12) });
    });
    for (let tick = 0; tick <= 4; tick++) {
      const y = graphHeight * tick / 4;
      line([left, y, front, left, y, back, right, y, back], '#262930', gridGroup);
      axisLabels.push({ id: `hours-${tick}`, kind: 'hours', datum: source(maximumHours * tick / 4, 'hours_per_year; fixed baseline axis scale, not an observation'), point: new Vector3(left - 0.15, y, front) });
    }
    selected = Math.min(selected, samples.length - 1);
    syncSelection();
    initialized = true;
    if (canAnimate) {
      transition = { from: new Float32Array(previous), to: data.positions, started: performance.now() };
      frame = window.requestAnimationFrame(animate);
    } else transition = undefined;
    render();
  }

  function select(index: number) {
    selected = Math.max(0, Math.min(index, samples.length - 1));
    syncSelection();
    render();
  }

  function pick(event: PointerEvent) {
    if (!points || !mesh || !samples.length || transition) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    let index = raycaster.intersectObject(points)[0]?.index;
    if (index === undefined) {
      const hit = raycaster.intersectObject(mesh)[0];
      if (hit?.face) {
        index = [hit.face.a, hit.face.b, hit.face.c].sort((a, b) => new Vector3().fromArray(positions, a * 3).distanceToSquared(hit.point) - new Vector3().fromArray(positions, b * 3).distanceToSquared(hit.point))[0];
      }
    }
    if (index !== undefined && index !== selected) { select(index); callbacks.select(index); }
  }
  let pointerStart = { x: 0, y: 0 };
  const down = (event: PointerEvent) => { pointerStart = { x: event.clientX, y: event.clientY }; };
  const move = (event: PointerEvent) => { if (event.buttons === 0) pick(event); };
  const up = (event: PointerEvent) => { if (Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) < 5) pick(event); };
  const lost = (event: Event) => { event.preventDefault(); callbacks.unavailable(); };
  renderer.domElement.addEventListener('pointerdown', down);
  renderer.domElement.addEventListener('pointermove', move);
  renderer.domElement.addEventListener('pointerup', up);
  renderer.domElement.addEventListener('webglcontextlost', lost);
  controls.addEventListener('change', render);

  function resize() {
    width = Math.max(1, host.clientWidth);
    height = Math.max(1, host.clientHeight);
    const aspect = width / height;
    const span = Math.max(10.5, 15 / aspect);
    camera.top = span / 2;
    camera.bottom = -span / 2;
    camera.left = -span * aspect / 2;
    camera.right = span * aspect / 2;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    render();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  function reset() {
    camera.position.set(12, 9, 13);
    camera.zoom = 1;
    camera.updateProjectionMatrix();
    controls.target.set(0, 1.7, 0);
    controls.update();
    render();
  }
  function rotate(horizontal: number, vertical = 0) {
    const spherical = new Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    spherical.theta = Math.max(controls.minAzimuthAngle, Math.min(controls.maxAzimuthAngle, spherical.theta + horizontal));
    spherical.phi = Math.max(controls.minPolarAngle, Math.min(controls.maxPolarAngle, spherical.phi + vertical));
    camera.position.copy(controls.target).add(new Vector3().setFromSpherical(spherical));
    controls.update();
    render();
  }
  function zoom(factor: number) {
    camera.zoom = Math.max(controls.minZoom, Math.min(controls.maxZoom, camera.zoom * factor));
    camera.updateProjectionMatrix();
    render();
  }
  reset();
  resize();

  return { update, select, reset, rotate, zoom, dispose() {
    disposed = true;
    if (frame !== undefined) window.cancelAnimationFrame(frame);
    observer.disconnect();
    controls.removeEventListener('change', render);
    controls.dispose();
    renderer.domElement.removeEventListener('pointerdown', down);
    renderer.domElement.removeEventListener('pointermove', move);
    renderer.domElement.removeEventListener('pointerup', up);
    renderer.domElement.removeEventListener('webglcontextlost', lost);
    clearGroup(dataGroup);
    clearGroup(gridGroup);
    renderer.dispose();
    renderer.forceContextLoss();
    renderer.domElement.remove();
  } };
}
