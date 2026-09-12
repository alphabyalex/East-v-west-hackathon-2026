import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { defaultInputs, deriveScenario, mockResponse, type ScenarioInputs, type Source, type SourcedInputs } from './model';

function useScenarioState() {
  const [inputs, setInputs] = useState<ScenarioInputs>({ ...defaultInputs });
  const [edited, setEdited] = useState<Set<keyof ScenarioInputs>>(new Set());
  const result = useMemo(() => {
    const derived = deriveScenario(inputs);
    // Keep exported input provenance identical to the controls on screen.
    derived.inputs = Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, {
      value,
      source_type: edited.has(key as keyof ScenarioInputs) ? 'assumption' : mockResponse.defaults[key as keyof ScenarioInputs].source_type,
      ref: edited.has(key as keyof ScenarioInputs) ? `user://scenario/${key}` : mockResponse.defaults[key as keyof ScenarioInputs].ref,
    }])) as SourcedInputs;
    return derived;
  }, [inputs, edited]);
  function update<K extends keyof ScenarioInputs>(key: K, value: ScenarioInputs[K]) {
    setInputs(previous => ({ ...previous, [key]: value }));
    setEdited(previous => new Set(previous).add(key));
  }
  function reset() {
    setInputs({ ...defaultInputs });
    setEdited(new Set());
  }
  function sourceFor(key: keyof ScenarioInputs): Source {
    return edited.has(key)
      ? { source_type: 'assumption', ref: `user://scenario/${key}` }
      : mockResponse.defaults[key];
  }
  return { inputs, result, update, reset, sourceFor };
}

const ScenarioContext = createContext<ReturnType<typeof useScenarioState> | null>(null);

export function ScenarioProvider({ children }: { children: ReactNode }) {
  return <ScenarioContext.Provider value={useScenarioState()}>{children}</ScenarioContext.Provider>;
}

export function useScenario() {
  const context = useContext(ScenarioContext);
  if (!context) throw new Error('useScenario must be used within ScenarioProvider');
  return context;
}
