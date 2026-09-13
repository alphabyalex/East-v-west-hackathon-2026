import React, { useEffect, useState } from 'react'
import { Award, Leaf, Wind, ShieldAlert, ChevronDown, ChevronUp, ClipboardList, Info } from 'lucide-react'

interface ZoneRankingItem {
  location_id: string
  avg_p50_risk_hours: number
  avg_p90_risk_hours: number
  avg_p99_risk_hours: number
  avg_worst_contiguous_hours: number
  wind_absorption_mwh_per_year: number
  carbon_absorbed_tonnes_per_year: number
  wind_source_ref: string
  score_risk: number
  score_wind: number
  score_carbon: number
  composite_score: number
  rank: number
}

interface ZoneRankingsResponse {
  operator: string
  composite_weight_formula: string
  description: string
  rankings: ZoneRankingItem[]
}

const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

export function ZoneLeaderboard() {
  const [data, setData] = useState<ZoneRankingsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  useEffect(() => {
    async function fetchRankings() {
      try {
        const response = await fetch('http://127.0.0.1:8000/api/zone-rankings')
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`)
        }
        const json = await response.json()
        setData(json)
      } catch (err: any) {
        setError(err.message || 'Failed to fetch rankings')
      } finally {
        setLoading(false)
      }
    }
    fetchRankings()
  }, [])

  const toggleRow = (locationId: string) => {
    setExpandedRow(prev => (prev === locationId ? null : locationId))
  }

  if (loading) {
    return (
      <div className="panel leaderboard-panel loading-state" role="status">
        <span className="eyebrow loading-text">LOADING COGNITIVE SPATIAL LEADERBOARD...</span>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="panel leaderboard-panel error-state" role="alert">
        <span className="eyebrow error-text">LEADERBOARD OFFLINE: {error || 'No data available'}</span>
        <p className="field-note">Make sure your local FastAPI backend is running and site_rank pipeline has been precomputed.</p>
      </div>
    )
  }

  return (
    <section className="panel leaderboard-panel" aria-labelledby="leaderboard-title">
      <div className="panel-heading">
        <div className="flex items-center gap-2">
          <Award size={15} className="text-teal" />
          <h2 id="leaderboard-title">Optimal Zones for Flexible Computes</h2>
        </div>
        <span className="eyebrow muted">COGNITIVE SPATIAL SEARCH LEADERBOARD</span>
      </div>

      <div className="leaderboard-intro">
        <p>{data.description}</p>
        <div className="formula-badge">
          <ClipboardList size={11} />
          <span>Composite Score Formula: <code>{data.composite_weight_formula}</code></span>
        </div>
      </div>

      <div className="leaderboard-table-wrap" tabIndex={0} aria-label="Spatial rankings list">
        <table className="leaderboard-table">
          <thead>
            <tr>
              <th scope="col" className="text-center w-12">Rank</th>
              <th scope="col" className="text-left">Zone / Node ID</th>
              <th scope="col" className="text-center w-40">Risk Factor Score</th>
              <th scope="col" className="text-center w-40">Wind Potential Score</th>
              <th scope="col" className="text-center w-40">Carbon Offset Score</th>
              <th scope="col" className="text-right w-24">Composite</th>
              <th scope="col" className="w-10"></th>
            </tr>
          </thead>
          <tbody>
            {data.rankings.map(item => {
              const isExpanded = expandedRow === item.location_id
              const displayLabel = item.location_id.startsWith('spp-')
                ? item.location_id.replace('spp-', '').replace('-demo', '').toUpperCase() + ' (Illustrative)'
                : item.location_id + ' BA Zone'

              return (
                <React.Fragment key={item.location_id}>
                  <tr 
                    className={`leaderboard-row ${isExpanded ? 'active' : ''}`}
                    onClick={() => toggleRow(item.location_id)}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && toggleRow(item.location_id)}
                    role="button"
                    aria-expanded={isExpanded}
                    aria-label={`Rank ${item.rank}: ${displayLabel}, Composite Score: ${item.composite_score}`}
                  >
                    <td className="text-center font-mono text-teal bold">#{item.rank}</td>
                    <td className="text-left font-sans font-medium text-primary">
                      {displayLabel}
                    </td>
                    <td className="text-center">
                      <div className="score-meter-container">
                        <div className="score-meter-bar" style={{ width: `${item.score_risk * 100}%`, background: 'var(--teal)' }} />
                        <span className="score-meter-value">{(item.score_risk * 100).toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="text-center">
                      <div className="score-meter-container">
                        <div className="score-meter-bar" style={{ width: `${item.score_wind * 100}%`, background: 'var(--accent)' }} />
                        <span className="score-meter-value">{(item.score_wind * 100).toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="text-center">
                      <div className="score-meter-container">
                        <div className="score-meter-bar" style={{ width: `${item.score_carbon * 100}%`, background: 'var(--text-mint)' }} />
                        <span className="score-meter-value">{(item.score_carbon * 100).toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="text-right font-mono text-primary bold">{item.composite_score.toFixed(1)}</td>
                    <td className="text-center text-muted">
                      {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </td>
                  </tr>

                  {isExpanded && (
                    <tr className="leaderboard-expanded-row">
                      <td colSpan={7}>
                        <div className="leaderboard-expanded-card">
                          <h4>
                            <Info size={12} className="text-teal" />
                            Diagnostic Breakdown for {item.location_id}
                          </h4>
                          <div className="leaderboard-breakdown-grid">
                            <div className="breakdown-col">
                              <span className="breakdown-label flex items-center gap-1"><ShieldAlert size={11} className="text-amber" /> CURTAILMENT RISK</span>
                              <dl className="breakdown-details">
                                <dt>p50 (Median)</dt>
                                <dd>{decimal.format(item.avg_p50_risk_hours)} h/yr</dd>
                                <dt>p90 (Upper-Tail)</dt>
                                <dd>{decimal.format(item.avg_p90_risk_hours)} h/yr</dd>
                                <dt>p99 (Extreme)</dt>
                                <dd>{decimal.format(item.avg_p99_risk_hours)} h/yr</dd>
                                <dt>Worst Contiguous</dt>
                                <dd>{integer.format(item.avg_worst_contiguous_hours)} h/yr</dd>
                              </dl>
                            </div>
                            <div className="breakdown-col">
                              <span className="breakdown-label flex items-center gap-1"><Wind size={11} className="text-teal" /> RENEWABLE WIND ALIGNMENT</span>
                              <dl className="breakdown-details">
                                <dt>Wind Absorption</dt>
                                <dd>{integer.format(item.wind_absorption_mwh_per_year)} MWh/yr</dd>
                                <dt>Data Sourcing</dt>
                                <dd className="text-muted text-wrap-any">{item.wind_source_ref}</dd>
                              </dl>
                            </div>
                            <div className="breakdown-col">
                              <span className="breakdown-label flex items-center gap-1"><Leaf size={11} className="text-mint" /> ENVIRONMENTAL DECARBONIZATION</span>
                              <dl className="breakdown-details">
                                <dt>CO2 Prevented</dt>
                                <dd className="text-mint">{integer.format(item.carbon_absorbed_tonnes_per_year)} tonnes/yr</dd>
                                <dt>Methodology</dt>
                                <dd className="text-muted">Calculated as 0.45 tCO2 displaced per MWh wind integrated.</dd>
                              </dl>
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
