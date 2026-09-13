import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { adaptEstimateResponse, createMockEstimate, defaultInputs, deriveScenario, mockResponse, toEstimateRequest, type ScenarioInputs, type Source, type SourcedInputs } from './model';
import { useEstimateTransport, type EstimateMode } from './hooks/useEstimateTransport';
import { validateEconomicsAssumptions, type EconomicsAssumptions } from './api/assumptions';
import economicSnapshot from './model/economics-assumptions.json';
import { buildSensitivity } from './model/sensitivity';
import { useLocationEstimator } from './hooks/useLocationEstimator';
import type { LocationResult } from './api/locationEstimator';

const offlineAssumptions = validateEconomicsAssumptions(economicSnapshot);

const economicKeys = ['firm_wait_years', 'gpu_per_mw', 'gpu_hour_value_usd', 'early_margin_usd_per_mw_year'] as const;
const economicFields = {
  firm_wait_years: 'early_connection_years',
  gpu_per_mw: 'gpus_per_mw',
  gpu_hour_value_usd: 'gpu_rental_price_usd_per_hour',
  early_margin_usd_per_mw_year: 'early_margin_usd_per_mw_year',
  vpp_battery_discharge_mw_per_home: 'vpp_battery_discharge_mw_per_home',
  vpp_arbitrage_revenue_usd_per_mwh: 'vpp_arbitrage_revenue_usd_per_mwh',
} as const;
const configuredMode = (): EstimateMode => import.meta.env.VITE_ESTIMATE_MODE === 'local' ? 'local' : 'api';

export type SavedScenario = {
  id: string
  name: string
  timestamp: number
  inputs: ScenarioInputs
} & ({ result: ReturnType<typeof deriveScenario>; locationEstimate?: undefined; locationQuery?: undefined }
  | { result?: undefined; locationEstimate: LocationResult; locationQuery: string });

function useScenarioState(initialMode: EstimateMode) {
  const [storedInputs, setInputs] = useState<ScenarioInputs>({ ...defaultInputs });
  const [edited, setEdited] = useState<Set<keyof ScenarioInputs>>(new Set());
  const [mode, setMode] = useState<EstimateMode>(initialMode);
  const [modeNote, setModeNote] = useState('');
  const [serverDefaults, setServerDefaults] = useState<EconomicsAssumptions>();
  const [savedScenarios, setSavedScenarios] = useState<SavedScenario[]>(() => {
    try {
      const saved = localStorage.getItem('fluxline_saved_scenarios');
      if (saved) return JSON.parse(saved);
    } catch {
      // Ignore in tests
    }
    return [];
  });

  useEffect(() => {
    try {
      localStorage.setItem('fluxline_saved_scenarios', JSON.stringify(savedScenarios));
    } catch {
      // Ignore in tests
    }
  }, [savedScenarios]);

  const request = useMemo(() => {
    const r = toEstimateRequest(storedInputs);
    r.vpp_solar_homes = storedInputs.vpp_solar_homes;
    return r;
  }, [storedInputs]);
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
  const location = useLocationEstimator(inputs);
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
      : deriveScenario(inputs, decisionPolicy);
    // Keep exported input provenance identical to the controls on screen.
    derived.inputs = Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, {
      value,
      ...sourceFor(key as keyof ScenarioInputs),
    }])) as SourcedInputs;
    return derived;
  }, [inputs, edited, transport.response, economicDefaults, decisionPolicy]);
  const sensitivity = useMemo(() => transport.sensitivity ?? buildSensitivity(
    result.canonical_response,
    createMockEstimate({ ...toEstimateRequest(inputs), site_exposure: 1 }, inputs, decisionPolicy),
    inputs,
    economicDefaults ?? offlineAssumptions,
  ), [transport.sensitivity, result, inputs, decisionPolicy, economicDefaults]);

  function saveScenario(name?: string) {
    if (location.enabled && !location.result) return;
    const defaultLabel = `Scenario ${savedScenarios.length + 1}: ${
      inputs.location_id === 'SPP_SYSTEM'
        ? 'SPP System'
        : inputs.location_id.replace('spp-', '').replace('-demo', '').toUpperCase()
    } (${inputs.load_mw} MW)`;
    const newScenario: SavedScenario = {
      id: `scen_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
      name: name || (location.result ? `${location.result.location.name} (${inputs.load_mw} MW)` : defaultLabel),
      timestamp: Date.now(),
      inputs: { ...inputs },
      ...(location.enabled && location.result ? { locationEstimate: location.result, locationQuery: location.query } : { result: { ...result } }),
    };
    setSavedScenarios(prev => [...prev, newScenario]);
  }

  function deleteScenario(id: string) {
    setSavedScenarios(prev => prev.filter(scen => scen.id !== id));
  }

  function loadScenario(id: string) {
    const target = savedScenarios.find(scen => scen.id === id);
    if (target) {
      location.reset();
      setInputs({ ...target.inputs });
      const editedKeys = Object.keys(target.inputs).filter(key => {
        const val = target.inputs[key as keyof ScenarioInputs];
        const def = defaultInputs[key as keyof ScenarioInputs];
        return val !== def;
      });
      setEdited(new Set(editedKeys as (keyof ScenarioInputs)[]));
      if (target.locationEstimate) {
        setEdited(new Set(Object.keys(target.inputs) as (keyof ScenarioInputs)[]));
        location.restore(target.locationEstimate, target.locationQuery);
      }
    }
  }

  function clearAllScenarios() {
    setSavedScenarios([]);
  }
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
      setModeNote('API mode reads economic assumptions from the backend, including any unverified placeholders. Local overrides have been reset.');
      transport.retry();
    } else {
      setModeNote('Local mock mode works without the backend. No estimate requests are sent.');
    }
    setMode(next);
  }
  function reset() {
    location.reset();
    setInputs({ ...defaultInputs });
    setEdited(new Set());
    setModeNote('');
  }
  return {
    location,
    inputs,
    result,
    sensitivity,
    update,
    reset,
    sourceFor,
    mode,
    chooseMode,
    modeNote,
    status: transport.status,
    error: transport.error,
    retry: transport.retry,
    savedScenarios,
    saveScenario,
    deleteScenario,
    loadScenario,
    clearAllScenarios,
  };
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
