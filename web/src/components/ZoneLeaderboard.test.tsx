// @vitest-environment jsdom
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ZoneLeaderboard } from './ZoneLeaderboard'
import shippedManifest from '../../../data/processed/national_stack/zone_rankings.json'

const mockRankings = {
  operator: "SPP",
  composite_weight_formula: "0.5*S_risk + 0.3*S_wind + 0.2*S_carbon",
  description: "Composite sustainability and grid compatibility score per SPP balancing authority zone.",
  rankings: [
    {
      location_id: "OKGE",
      avg_p50_risk_hours: 33.57,
      avg_p90_risk_hours: 95.85,
      avg_p99_risk_hours: 134.72,
      avg_worst_contiguous_hours: 6.0,
      wind_absorption_mwh_per_year: 125271.55,
      carbon_absorbed_tonnes_per_year: 56372.2,
      wind_source_ref: "mock://illustrative/wind-absorption/sourcing-pending-merge",
      score_risk: 0.7,
      score_wind: 1.0,
      score_carbon: 1.0,
      composite_score: 85.0,
      rank: 1
    },
    {
      location_id: "spp-wichita-demo",
      avg_p50_risk_hours: 33.28,
      avg_p90_risk_hours: 86.0,
      avg_p99_risk_hours: 121.15,
      avg_worst_contiguous_hours: 7.0,
      wind_absorption_mwh_per_year: 112068.16,
      carbon_absorbed_tonnes_per_year: 50430.67,
      wind_source_ref: "mock://illustrative/wind-absorption/sourcing-pending-merge",
      score_risk: 0.71,
      score_wind: 0.88,
      score_carbon: 0.88,
      composite_score: 79.65,
      rank: 2
    }
  ]
}

describe('ZoneLeaderboard', () => {
  beforeEach(() => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url) => {
      if (String(url).endsWith('/api/zone-rankings')) {
        return Promise.resolve(new Response(JSON.stringify(mockRankings), { status: 200 }))
      }
      return Promise.reject(new Error('Unknown URL'))
    }))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders loading state initially', () => {
    render(<ZoneLeaderboard />)
    expect(screen.getByText(/LOADING COGNITIVE SPATIAL LEADERBOARD/i)).toBeTruthy()
  })

  it('renders spatial rankings successfully after API load', async () => {
    render(<ZoneLeaderboard />)
    
    // Wait for the rankings table to load
    await waitFor(() => {
      expect(screen.getByText('Optimal Zones for Flexible Computes')).toBeTruthy()
    })

    // Assert rows and ranking metrics
    expect(screen.getByText('OKGE BA Zone')).toBeTruthy()
    expect(screen.getByText('WICHITA (Illustrative)')).toBeTruthy()
    
    // Check composite scores are rendered
    expect(screen.getByText('85.0')).toBeTruthy()
    expect(screen.getByText('79.7')).toBeTruthy()
    
    // Check rank indicators
    expect(screen.getByText('#1')).toBeTruthy()
    expect(screen.getByText('#2')).toBeTruthy()
  })

  it('toggles expansion diagnostics on row click', async () => {
    render(<ZoneLeaderboard />)
    
    await waitFor(() => {
      expect(screen.getByText('OKGE BA Zone')).toBeTruthy()
    })

    // Diagnostics should be hidden initially
    expect(screen.queryByText(/Diagnostic Breakdown for OKGE/i)).toBeNull()

    // Click OKGE row to expand
    fireEvent.click(screen.getByText('OKGE BA Zone'))
    
    // Diagnostics card should now be visible
    expect(screen.getByText(/Diagnostic Breakdown for OKGE/i)).toBeTruthy()
    
    // Check specific breakdowns
    expect(screen.getByText('33.57 h/yr')).toBeTruthy() // avg_p50
    expect(screen.getByText('56,372 tonnes/yr')).toBeTruthy() // carbon offset
    
    // Click OKGE row again to collapse
    fireEvent.click(screen.getByText('OKGE BA Zone'))
    
    // Diagnostics should be hidden again
    expect(screen.queryByText(/Diagnostic Breakdown for OKGE/i)).toBeNull()
  })

  it('renders error state on API failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('Connection timeout')))
    render(<ZoneLeaderboard />)
    
    await waitFor(() => {
      expect(screen.getByText(/LEADERBOARD OFFLINE: Connection timeout/i)).toBeTruthy()
    })
  })

  it('renders the shipped wind evidence and explains every excluded location without invented ranks', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(shippedManifest), { status: 200 })))
    render(<ZoneLeaderboard />)
    expect(await screen.findByText('Composite ranking unavailable')).toBeTruthy()
    expect(screen.getByText('LES · LES_LES')).toBeTruthy()
    expect(screen.getByText('OKGE · OKGE_OKGE')).toBeTruthy()
    expect(screen.queryByLabelText('Spatial rankings list')).toBeNull()
    expect(screen.queryByText('#1')).toBeNull()
    fireEvent.click(screen.getByText('Excluded locations and missing evidence'))
    for (const item of shippedManifest.excluded_locations) {
      expect(screen.getByRole('button', { name: new RegExp(`^${item.location_id}.*data provenance`) })).toBeTruthy()
    }
    const les = shippedManifest.available_wind_evidence.find(row => row.location_id === 'LES')!
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${les.proxy_hours.value}.*assumption provenance`) }))
    const source = JSON.parse(screen.getByRole('tooltip').getAttribute('data-provenance')!)
    expect(source).toEqual(les.proxy_hours)
  })
})
