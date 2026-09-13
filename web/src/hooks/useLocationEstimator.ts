import { useEffect, useRef, useState } from 'react';
import type { ScenarioInputs } from '../model';
import { locationRequest, validateLocationResult, waitForLocationJob, type LocationPoint, type LocationReply, type LocationResult } from '../api/locationEstimator';

interface Selection { query: string; scan: string; candidates: LocationPoint[]; index: number | null }

export function useLocationEstimator(inputs: ScenarioInputs) {
  const [enabled, setEnabled] = useState(false);
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [selection, setSelection] = useState<Selection>();
  const [completed, setCompleted] = useState<{ key: string; result: LocationResult }>();
  const [phase, setPhase] = useState('idle');
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const key = JSON.stringify([query.trim().toLowerCase(), inputs]);
  const currentKey = useRef(key);
  currentKey.current = key;
  const result = enabled && completed?.key === key ? completed.result : undefined;

  useEffect(() => {
    controller.current?.abort();
    if (enabled) { setPhase('idle'); setError(''); }
    return () => controller.current?.abort();
  }, [key, enabled]);

  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    void locationRequest<{ locations: string[] }>('/locations', abort.signal)
      .then(data => { if (Array.isArray(data.locations)) setSuggestions(data.locations); })
      .catch(() => { /* Suggestions are optional; an explicit search reports errors. */ });
    return () => abort.abort();
  }, [enabled]);

  async function submit(index?: number) {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const capturedKey = key;
    const capturedInputs = { ...inputs };
    const active = () => !abort.signal.aborted && currentKey.current === capturedKey;
    setError('');
    setCompleted(undefined);
    try {
      if (query.trim().length < 2) throw new Error('Enter a city and state, or latitude and longitude.');
      let selected = selection?.query === query.trim().toLowerCase() ? selection : undefined;
      if (!selected) {
        setPhase('searching');
        let search = await locationRequest<LocationReply>('/search', abort.signal, { query: query.trim() });
        if (search.status === 'running') {
          await waitForLocationJob(search, abort.signal);
          search = await locationRequest<LocationReply>('/search', abort.signal, { query: query.trim() });
        }
        if (!active()) return;
        if (!search.scan_id || !search.candidates?.length) throw new Error('No matching location found. Try a city and state, or coordinates.');
        selected = { query: query.trim().toLowerCase(), scan: search.scan_id, candidates: search.candidates, index: search.candidates.length === 1 ? 0 : null };
        setSelection(selected);
      }
      const candidate = index ?? selected.index;
      if (candidate === null) { setPhase('choosing'); return; }
      selected = { ...selected, index: candidate };
      setSelection(selected);
      setPhase('estimating');
      const payload = { scan_id: selected.scan, candidate, inputs: capturedInputs };
      let estimate = await locationRequest<LocationReply>('/estimate', abort.signal, payload);
      if (estimate.status === 'running') {
        const finished = await waitForLocationJob(estimate, abort.signal);
        estimate = await locationRequest<LocationReply>('/estimate', abort.signal, { ...payload, report_id: finished.result_id });
      }
      if (!active()) return;
      setCompleted({ key: capturedKey, result: validateLocationResult(estimate.result, capturedInputs) });
      setPhase('complete');
    } catch (cause) {
      if (active()) { setError(cause instanceof Error ? cause.message : 'Could not estimate this location. Retry.'); setPhase('error'); }
    }
  }

  function changeQuery(value: string) {
    controller.current?.abort();
    setQuery(value);
    setSelection(undefined);
  }
  function activate(value: boolean) {
    controller.current?.abort();
    setEnabled(value);
  }
  function reset() {
    activate(false); changeQuery(''); setCompleted(undefined); setError(''); setPhase('idle');
  }
  function restore(saved: LocationResult, savedQuery: string) {
    controller.current?.abort(); setEnabled(true); setQuery(savedQuery); setSelection(undefined);
    setCompleted({ key: JSON.stringify([savedQuery.trim().toLowerCase(), saved.inputs_echo]), result: saved });
    setPhase('complete'); setError('');
  }
  return { enabled, activate, query, changeQuery, suggestions, selection, result, phase, error, submit, reset, restore,
    stale: enabled && !!completed && !result, busy: phase === 'searching' || phase === 'estimating' };
}
