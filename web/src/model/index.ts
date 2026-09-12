import { createMockEstimate, adaptEstimateResponse, toEstimateRequest } from './estimate'
import type { ScenarioInputs, ScenarioResult } from './types'

export { defaultInputs, mockAssumption, mockResponse } from './fixture'
export { createMockEstimate, adaptEstimateResponse, toEstimateRequest } from './estimate'
export type * from './types'
export type * from './contract'

/** Offline transport stub. The UI consumes the same response adapter as a future API. */
export function deriveScenario(inputs: ScenarioInputs): ScenarioResult {
  return adaptEstimateResponse(createMockEstimate(toEstimateRequest(inputs), inputs), inputs)
}
