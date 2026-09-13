import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { adaptEstimateResponse, defaultInputs, mockResponse, toEstimateRequest, type ScenarioInputs, type Source, type SourcedInputs } from './model';
import { createLocationPreview } from './model/location-preview';
import { useEstimateTransport, type EstimateMode } from './hooks/useEstimateTransport';
import { validateEconomicsAssumptions, type EconomicsAssumptions } from './api/assumptions';
import economicSnapshot from './model/economics-assumptions.json';
import { buildSensitivity } from './model/sensitivity';

const offlineAssumptions = validateEconomicsAssumptions(economicSnapshot);

const economicKeys = ['firm_wait_years', 'gpu_per_mw', 'gpu_hour_value_usd', 'early_margin_usd_per_mw_year'] as const;
const economicFields = {
  firm_wait_years: 'early_connection_years',
  gpu_per_mw: 'gpus_per_mw',
  gpu_hour_value_usd: 'gpu_rental_price_usd_per_hour',
  early_margin_usd_per_mw_year: 'early_margin_usd_per_mw_year',
} as const;
const configuredMode = (): EstimateMode => import.meta.env.VITE_ESTIMATE_MODE === 'local' ? 'local' : 'api';

function useScenarioState(initialMode: EstimateMode) {
  const [storedInputs, setInputs] = useState<ScenarioInputs>({ ...defaultInputs });
  const [edited, setEdited] = useState<Set<keyof ScenarioInputs>>(new Set());
  const [mode, setMode] = useState<EstimateMode>(initialMode);
  const [modeNote, setModeNote] = useState('');
  const [serverDefaults, setServerDefaults] = useState<EconomicsAssumptions>();
  const request = useMemo(() => toEstimateRequest(storedInputs), [storedInputs]);
  const transport = useEstimateTransport(request, mode);
  useEffect(() => {
    if (transport.assumptions) setServerDefaults(transport.assumptions);
  }, [transport.assumptions]);
  const economicDefaults = transport.assumptions ?? serverDefaults;
  const inputs = useMemo(() => {
    const values = { ...storedInputs };
    if (economicDefaults) {
      for (const key of economicKeys) {
        if (!edited.has(key)) values[key] = economicDefaults[economicFields[key]].value;
      }
    }
    return values;
  }, [storedInputs, economicDefaults, edited]);
  const decisionPolicy = economicDefaults?.close_call_fraction ?? mockResponse.decision_policy.close_call_fraction;
  function sourceFor(key: keyof ScenarioInputs): Source {
    if (edited.has(key)) return { source_type: 'assumption', ref: `user://scenario/${key}` };
    if (economicDefaults && key in economicFields) {
      return economicDefaults[economicFields[key as keyof typeof economicFields]];
    }
    return mockResponse.defaults[key];
  }
  const result = useMemo(() => {
    // Never leave an older server response beneath newly edited controls.
    const derived = transport.response
      ? adaptEstimateResponse(transport.response, inputs, decisionPolicy)
      : adaptEstimateResponse(createLocationPreview(toEstimateRequest(inputs), inputs, decisionPolicy), inputs, decisionPolicy);
    // Keep exported input provenance identical to the controls on screen.
    derived.inputs = Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, {
      value,
      ...sourceFor(key as keyof ScenarioInputs),
    }])) as SourcedInputs;
    return derived;
  }, [inputs, edited, transport.response, economicDefaults, decisionPolicy]);
  const sensitivity = useMemo(() => transport.sensitivity ?? buildSensitivity(
    result.canonical_response,
    createLocationPreview({ ...toEstimateRequest(inputs), site_exposure: 1 }, inputs, decisionPolicy),
    inputs,
    economicDefaults ?? offlineAssumptions,
  ), [transport.sensitivity, result, inputs, decisionPolicy, economicDefaults]);
  function update<K extends keyof ScenarioInputs>(key: K, value: ScenarioInputs[K]) {
    if (inputs[key] === value) return;
    if ((economicKeys as readonly (keyof ScenarioInputs)[]).includes(key)) {
      setMode('local');
      setModeNote('Economic input changed. Assumed scenario values now apply your overrides; connected estimates use the supplied defaults.');
    }
    setInputs(previous => ({ ...previous, [key]: value }));
    setEdited(previous => new Set(previous).add(key));
  }
  function chooseMode(next: EstimateMode) {
    if (next === 'api') {
      setInputs(previous => ({ ...previous, ...Object.fromEntries(economicKeys.map(key => [key, defaultInputs[key]])) }));
      setEdited(previous => new Set([...previous].filter(key => !(economicKeys as readonly (keyof ScenarioInputs)[]).includes(key))));
      setModeNote('Connected estimates use supplied economic assumptions, including any unverified inputs. Your economic overrides have been reset.');
      transport.retry();
    } else {
      setModeNote('Assumed scenario values work offline. No connected estimate is requested.');
    }
    setMode(next);
  }
  function reset() {
    setInputs({ ...defaultInputs });
    setEdited(new Set());
    setModeNote('');
  }
  return { inputs, result, sensitivity, update, reset, sourceFor, mode, chooseMode, modeNote, status: transport.status, error: transport.error, retry: transport.retry };
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
