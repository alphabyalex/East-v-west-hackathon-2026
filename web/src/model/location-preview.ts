import { createMockEstimate } from './estimate'
import { mockResponse } from './fixture'
import type { EstimateRequest, MockEconomicInputs } from './contract'
import type { SourcedValue } from './types'

/** Keep new catalog selections usable while awaiting HTTP or working offline.
 * This reuses the existing generic fixture, never invents a zone-specific model.
 * All fixture arithmetic is unchanged and all response blocks remain mock-sourced.
 */
export function createLocationPreview(
  request: EstimateRequest,
  economics: Partial<MockEconomicInputs> = {},
  decisionPolicy?: SourcedValue,
) {
  if (mockResponse.locations.some(location => location.id === request.location_id)) {
    return createMockEstimate(request, economics, decisionPolicy)
  }
  const preview = createMockEstimate({ ...request, location_id: 'SPP_SYSTEM' }, economics, decisionPolicy)
  preview.inputs_echo = { ...request }
  const explanation = `; placeholder preview for requested_location_id=${encodeURIComponent(request.location_id)}; `
    + 'uses the SPP_SYSTEM mock fixture, not fitted exposure for this zone'
  preview.modeled_exposure.source.ref += explanation
  preview.economics.source.ref += explanation
  return preview
}
