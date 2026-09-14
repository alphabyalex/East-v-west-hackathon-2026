import { useEffect, useState } from 'react'
import { ChevronDown, MapPin } from 'lucide-react'
import { useScenario } from '../ScenarioContext'
import { getLocations, offlineLocations, type LocationOption } from '../api/locations'
import { LocationSearchField } from './LocationEstimate'

export function LocationField({ showTelemetry, setShowTelemetry }: { showTelemetry: boolean; setShowTelemetry: (show: boolean) => void }) {
  const { inputs, update, sourceFor, mode, location: estimateLocation } = useScenario()
  const [locations, setLocations] = useState<LocationOption[]>(offlineLocations)
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    if (mode === 'local') return
    const controller = new AbortController()
    let disposed = false
    setUnavailable(false)
    const timeout = setTimeout(() => {
      if (!disposed) setUnavailable(true)
      controller.abort()
    }, 3000)
    void getLocations({ signal: controller.signal }).then(options => {
      if (!disposed && !controller.signal.aborted) setLocations(options)
    }).catch(() => {
      if (!disposed) setUnavailable(true)
    }).finally(() => clearTimeout(timeout))
    return () => {
      disposed = true
      clearTimeout(timeout)
      controller.abort()
    }
  }, [mode])

  const choices = locations.filter(location => location.kind !== 'system' && location.id !== 'SPP_SYSTEM')
  const selected = choices.find(location => location.id === inputs.location_id)
  const note = !selected ? ''
    : selected.kind === 'scenario' ? 'Scenario location · no site-specific grid data'
      : selected.kind === 'zone' ? 'SPP load zone · zone-specific model data; site exposure is your assumption'
        : 'SPP location · model coverage not confirmed'
  return <div className="location-field">
    <div className="field-label">
      <label htmlFor="location"><MapPin size={13} />SPP LOCATION</label>
    </div>
    <div className="select-wrap">
      <select
        id="location"
        data-provenance={inputs.location_id === 'SPP_SYSTEM' && !estimateLocation.enabled ? undefined : JSON.stringify({ value: estimateLocation.enabled ? estimateLocation.query : inputs.location_id, ...(estimateLocation.enabled ? { source_type: 'assumption', ref: 'user://location-query' } : sourceFor('location_id')) })}
        value={estimateLocation.enabled ? 'custom-location' : inputs.location_id === 'SPP_SYSTEM' ? '' : inputs.location_id}
        onChange={event => {
          const custom = event.target.value === 'custom-location';
          estimateLocation.activate(custom);
          if (custom) setShowTelemetry(false);
          else update('location_id', event.target.value || 'SPP_SYSTEM');
        }}
        aria-describedby="location-note"
      >
        <option value="" disabled>Select zone</option>
        <option value="custom-location">Choose a city or coordinates</option>
        {choices.map(location => <option key={location.id} value={location.id}>{location.label}</option>)}
        {!selected && inputs.location_id !== 'SPP_SYSTEM' && !estimateLocation.enabled && (
          <option value={inputs.location_id} disabled>
            {inputs.location_id} · not in current catalog
          </option>
        )}
      </select>
      <ChevronDown size={15} />
    </div>
    {estimateLocation.enabled ? (
      <LocationSearchField />
    ) : (
      <span className="field-note" id="location-note">
        {note}
        {unavailable ? ' · Location list unavailable; showing last loaded choices.' : ''}
      </span>
    )}
  </div>
}
