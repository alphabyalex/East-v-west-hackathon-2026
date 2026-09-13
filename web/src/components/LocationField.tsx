import { useEffect, useState } from 'react'
import { ChevronDown, MapPin } from 'lucide-react'
import { useScenario } from '../ScenarioContext'
import { getLocations, offlineLocations, type LocationOption } from '../api/locations'
import { SourceInfo } from './Sourced'

export function LocationField() {
  const { inputs, update, sourceFor, mode } = useScenario()
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

  const selected = locations.find(location => location.id === inputs.location_id)
  const note = selected?.kind === 'system' ? 'System aggregate · no site-specific grid data'
    : selected?.kind === 'scenario' ? 'Scenario location · no site-specific grid data'
      : selected?.kind === 'zone' ? 'SPP load zone · zone-specific model data; site exposure is your assumption'
        : 'SPP location · model coverage not confirmed'
  return <div className="location-field">
    <div className="field-label"><label htmlFor="location"><MapPin size={13} />SPP LOCATION</label><SourceInfo value={inputs.location_id} source={sourceFor('location_id')} label="Location provenance" /></div>
    <div className="select-wrap"><select id="location" value={inputs.location_id} onChange={event => update('location_id', event.target.value)} aria-describedby="location-note">
      {!selected && <option value={inputs.location_id} disabled>{inputs.location_id} · not in current catalog</option>}
      {locations.map(location => <option key={location.id} value={location.id}>{location.label}</option>)}
    </select><ChevronDown size={15} /></div>
    <span className="field-note" id="location-note">{note}{unavailable ? ' · Location list unavailable; showing last loaded choices.' : ''}</span>
  </div>
}
