import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { adaptEstimateResponse, defaultInputs, deriveScenario, mockResponse, toEstimateRequest, type ScenarioInputs, type Source, type SourcedInputs } from './model';
import { useEstimateTransport, type EstimateMode } from './hooks/useEstimateTransport';

const economicKeys = ['firm_wait_years', 'gpu_per_mw', 'gpu_hour_value_usd', 'early_margin_usd_per_mw_year'] as const;
const configuredMode = (): EstimateMode => import.meta.env.VITE_ESTIMATE_MODE === 'local' ? 'local' : 'api';

function useScenarioState(initialMode: EstimateMode) {
  const [inputs, setInputs] = useState<ScenarioInputs>({ ...defaultInputs });
  const [edited, setEdited] = useState<Set<keyof ScenarioInputs>>(new Set());
  const [mode, setMode] = useState<EstimateMode>(initialMode);
  const [modeNote, setModeNote] = useState('');
  const request = useMemo(() => toEstimateRequest(inputs), [inputs]);
  const transport = useEstimateTransport(request, mode);
  const result = useMemo(() => {
    // Never leave an older server response beneath newly edited controls.
    const derived = transport.response
      ? adaptEstimateResponse(transport.response, inputs)
      : deriveScenario(inputs);
    // Keep exported input provenance identical to the controls on screen.
    derived.inputs = Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, {
      value,
      source_type: edited.has(key as keyof ScenarioInputs) ? 'assumption' : mockResponse.defaults[key as keyof ScenarioInputs].source_type,
      ref: edited.has(key as keyof ScenarioInputs) ? `user://scenario/${key}` : mockResponse.defaults[key as keyof ScenarioInputs].ref,
    }])) as SourcedInputs;
    return derived;
  }, [inputs, edited, transport.response]);
  function update<K extends keyof ScenarioInputs>(key: K, value: ScenarioInputs[K]) {
    if (inputs[key] === value) return;
    if ((economicKeys as readonly (keyof ScenarioInputs)[]).includes(key)) {
      setMode('local');
      setModeNote('Economic input changed. Local mock mode applies your overrides; the API request does not include them.');
    }
    setInputs(previous => ({ ...previous, [key]: value }));
    setEdited(previous => new Set(previous).add(key));
  }
  function chooseMode(next: EstimateMode) {
    if (next === 'api') {
      setInputs(previous => ({ ...previous, ...Object.fromEntries(economicKeys.map(key => [key, defaultInputs[key]])) }));
      setEdited(previous => new Set([...previous].filter(key => !(economicKeys as readonly (keyof ScenarioInputs)[]).includes(key))));
      setModeNote('API mode uses the default mock economics. Local overrides have been reset.');
      transport.retry();
    } else {
      setModeNote('Local mock mode works without the backend. No estimate requests are sent.');
    }
    setMode(next);
  }
  function reset() {
    setInputs({ ...defaultInputs });
    setEdited(new Set());
    setModeNote('');
  }
  function sourceFor(key: keyof ScenarioInputs): Source {
    return edited.has(key)
      ? { source_type: 'assumption', ref: `user://scenario/${key}` }
      : mockResponse.defaults[key];
  }
  return { inputs, result, update, reset, sourceFor, mode, chooseMode, modeNote, status: transport.status, error: transport.error, retry: transport.retry };
}

const ScenarioContext = createContext<ReturnType<typeof useScenarioState> | null>(null);

export function ScenarioProvider({ children, initialMode = configuredMode() }: { children: ReactNode; initialMode?: EstimateMode }) {
  return <ScenarioContext.Provider value={useScenarioState(initialMode)}>{children}</ScenarioContext.Provider>;
}

export function useScenario() {
  const context = useContext(ScenarioContext);
  if (!context) throw new Error('useScenario must be used within ScenarioProvider');
  return context;
}
