import { useEffect, useMemo, useRef, useState } from 'react';
import { postEstimate } from '../api/client';
import type { EstimateRequest, EstimateResponse } from '../model';

export type EstimateMode = 'api' | 'local';
export type EstimateStatus = 'local' | 'loading' | 'api' | 'fallback';

const DEBOUNCE_MS = 50;
const TIMEOUT_MS = 3000;

interface Attempt {
  key: string;
  retry: number;
  response?: EstimateResponse;
  error?: string;
}

export function useEstimateTransport(request: EstimateRequest, mode: EstimateMode) {
  const key = JSON.stringify(request);
  const submitted = useMemo<EstimateRequest>(() => JSON.parse(key), [key]);
  const [retryCount, setRetryCount] = useState(0);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const sequence = useRef(0);

  useEffect(() => {
    const current = ++sequence.current;
    if (mode === 'local') return;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const active = () => sequence.current === current && !controller.signal.aborted;
    const debounce = setTimeout(() => {
      timeout = setTimeout(() => {
        if (!active()) return;
        setAttempt({ key, retry: retryCount, error: 'API request timed out. Showing the current scenario as a local mock.' });
        controller.abort();
      }, TIMEOUT_MS);
      void postEstimate(submitted, { signal: controller.signal }).then(response => {
        if (active()) setAttempt({ key, retry: retryCount, response });
      }).catch(() => {
        if (!active()) return;
        setAttempt({ key, retry: retryCount, error: 'API unavailable or returned an invalid estimate. Showing the current scenario as a local mock.' });
      }).finally(() => clearTimeout(timeout));
    }, DEBOUNCE_MS);
    return () => {
      ++sequence.current;
      clearTimeout(debounce);
      clearTimeout(timeout);
      controller.abort();
    };
  }, [key, submitted, mode, retryCount]);

  const matches = mode === 'api' && attempt?.key === key && attempt.retry === retryCount;
  const response = matches ? attempt.response : undefined;
  const error = matches ? attempt.error : undefined;
  const status: EstimateStatus = mode === 'local' ? 'local' : response ? 'api' : error ? 'fallback' : 'loading';
  return { response, error, status, retry: () => setRetryCount(previous => previous + 1) };
}
