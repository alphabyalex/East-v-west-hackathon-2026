import { useEffect, useMemo, useRef, useState } from 'react';
import { postEstimate } from '../api/client';
import { getEconomicsAssumptions, type EconomicsAssumptions } from '../api/assumptions';
import { buildEstimateSensitivity } from '../api/sensitivity';
import type { SensitivityResult } from '../model/sensitivity';
import type { EstimateRequest, EstimateResponse } from '../model';

export type EstimateMode = 'api' | 'local';
export type EstimateStatus = 'local' | 'loading' | 'api' | 'fallback';

const DEBOUNCE_MS = 50;
const TIMEOUT_MS = 3000;

interface Attempt {
  key: string;
  retry: number;
  response?: EstimateResponse;
  assumptions?: EconomicsAssumptions;
  sensitivity?: SensitivityResult;
  error?: string;
}

export function useEstimateTransport(request: EstimateRequest, mode: EstimateMode, enabled = true) {
  const key = JSON.stringify(request);
  const submitted = useMemo<EstimateRequest>(() => JSON.parse(key), [key]);
  const [retryCount, setRetryCount] = useState(0);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const sequence = useRef(0);

  useEffect(() => {
    const current = ++sequence.current;
    if (!enabled || mode === 'local') return;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const active = () => sequence.current === current && !controller.signal.aborted;
    const debounce = setTimeout(() => {
      timeout = setTimeout(() => {
        if (!active()) return;
        setAttempt({ key, retry: retryCount, error: 'Estimate request timed out. Showing assumed scenario values.' });
        controller.abort();
      }, TIMEOUT_MS);
      void Promise.all([
        postEstimate(submitted, { signal: controller.signal }),
        getEconomicsAssumptions({ signal: controller.signal }),
      ]).then(async ([response, assumptions]) => {
        if (!active()) return;
        const sensitivity = await buildEstimateSensitivity(response, assumptions, { signal: controller.signal });
        if (active()) setAttempt({ key, retry: retryCount, response, assumptions, sensitivity });
      }).catch(() => {
        if (!active()) return;
        setAttempt({ key, retry: retryCount, error: 'Estimate service unavailable or returned an invalid estimate. Showing assumed scenario values.' });
        controller.abort();
      }).finally(() => clearTimeout(timeout));
    }, DEBOUNCE_MS);
    return () => {
      ++sequence.current;
      clearTimeout(debounce);
      clearTimeout(timeout);
      controller.abort();
    };
  }, [key, submitted, mode, retryCount, enabled]);

  const matches = enabled && mode === 'api' && attempt?.key === key && attempt.retry === retryCount;
  const response = matches ? attempt.response : undefined;
  const error = matches ? attempt.error : undefined;
  const status: EstimateStatus = !enabled || mode === 'local' ? 'local' : response ? 'api' : error ? 'fallback' : 'loading';
  return { response, assumptions: matches ? attempt.assumptions : undefined, sensitivity: matches ? attempt.sensitivity : undefined, error, status, retry: () => setRetryCount(previous => previous + 1) };
}
