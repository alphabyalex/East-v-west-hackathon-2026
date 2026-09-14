import type { ScenarioInputs, Source } from '../model';

export interface LocationPoint { name: string; latitude: number; longitude: number }
export interface LocationResult {
  inputs_echo: ScenarioInputs;
  report_id: string;
  location: LocationPoint;
  model_version: string;
  created_utc: string;
  confidence: { level: 'Low' };
  location_data_note: string;
  limitations: string[];
  source: Source;
  assumption_source: Source;
  economic_source: Source;
  exposure: { annual_expected_hours: number; regional_expected_hours: number; term_expected_hours: number; annual_energy_mwh: number };
  economics: {
    interruptible_mw: number; vpp_offset_mw: number; net_interruptible_mw: number;
    annual_gpu_hours: number; vpp_annual_revenue_usd: number; annual_cost_usd: number;
    term_cost_usd: number; early_access_value_usd: number; net_value_usd: number; break_even_hours: number | null;
  };
  calculation_inputs: Record<string, unknown>;
  provenance: Record<string, unknown>;
}
export interface LocationReply {
  status: 'running' | 'succeeded' | 'failed';
  job_id?: string;
  result_id?: string;
  scan_id?: string;
  candidates?: LocationPoint[];
  result?: LocationResult;
}

const base = `${import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'}/api/location-estimator`;

export async function locationRequest<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(base + path, {
    method: body ? 'POST' : 'GET', signal: AbortSignal.any([signal, AbortSignal.timeout(25000)]),
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Location estimation is unavailable. Check your inputs and retry.');
  return data as T;
}

export function validateLocationResult(value: LocationResult | undefined, inputs: ScenarioInputs): LocationResult {
  if (!value || value.confidence?.level !== 'Low' || !value.location?.name || !value.report_id || !value.model_version
    || !value.source?.ref || !value.assumption_source?.ref || !value.economic_source?.ref
    || !Array.isArray(value.limitations) || !value.exposure || !value.economics
    || Object.entries(inputs).some(([key, input]) => value.inputs_echo?.[key as keyof ScenarioInputs] !== input)) {
    throw new Error('The location result does not match these inputs. Retry the estimate.');
  }
  for (const key of ['annual_expected_hours', 'regional_expected_hours', 'term_expected_hours', 'annual_energy_mwh'] as const) {
    if (!Number.isFinite(value.exposure[key]) || value.exposure[key] < 0) throw new Error('The location estimate contains invalid exposure values.');
  }
  for (const key of ['interruptible_mw', 'vpp_offset_mw', 'net_interruptible_mw', 'annual_gpu_hours', 'vpp_annual_revenue_usd', 'annual_cost_usd', 'term_cost_usd', 'early_access_value_usd', 'net_value_usd', 'break_even_hours'] as const) {
    if (key === 'break_even_hours' && value.economics[key] === null) continue;
    if (!Number.isFinite(value.economics[key])) throw new Error('The location estimate contains invalid economics.');
  }
  return value;
}

export async function waitForLocationJob(reply: LocationReply, signal: AbortSignal): Promise<LocationReply> {
  const deadline = Date.now() + 30 * 60 * 1000;
  if (!reply.job_id) throw new Error('The estimator did not return a job reference. Retry.');
  while (!signal.aborted && Date.now() < deadline) {
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 1500);
      signal.addEventListener('abort', abort, { once: true });
    });
    const status = await locationRequest<LocationReply>(`/jobs/${encodeURIComponent(reply.job_id)}`, signal);
    if (status.status === 'failed') throw new Error('This location estimate could not be completed. Retry or choose another location.');
    if (status.status === 'succeeded') return status;
  }
  throw new Error('The estimate is taking longer than expected. Retry to check for a completed result.');
}
