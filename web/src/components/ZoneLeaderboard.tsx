import React, { useEffect, useState, useMemo } from 'react'
import { Award, Leaf, Wind, ShieldAlert, ChevronDown, ChevronUp, ClipboardList, Info, Search, ArrowUpDown, LayoutGrid, BarChart3 } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'

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
  
  // Custom interactive state for sorting, filtering, and visual charts
  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState<'composite' | 'risk' | 'wind' | 'carbon'>('composite')
  const [viewMode, setViewMode] = useState<'table' | 'chart'>('table')

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

  // Filter & Sort rankings dynamically
  const sortedRankings = useMemo(() => {
    if (!data) return []
    return [...data.rankings]
      .filter(item => item.location_id.toLowerCase().includes(searchQuery.toLowerCase()))
      .sort((a, b) => {
        if (sortBy === 'composite') return b.composite_score - a.composite_score
        if (sortBy === 'risk') return a.score_risk - b.score_risk // Low risk is better!
        if (sortBy === 'wind') return b.score_wind - a.score_wind
        if (sortBy === 'carbon') return b.score_carbon - a.score_carbon
        return 0
      })
  }, [data, searchQuery, sortBy])

  // Map data specifically for Recharts BarChart
  const chartData = useMemo(() => {
    return sortedRankings.map(item => ({
      name: item.location_id.startsWith('spp-')
        ? item.location_id.replace('spp-', '').replace('-demo', '').toUpperCase()
        : item.location_id,
      'Composite Score': item.composite_score,
      original: item
    }))
  }, [sortedRankings])

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

      {/* Interactivity Bar: Filter, Sorting, and View Toggles */}
      <div className="leaderboard-controls flex items-center justify-between gap-4 wrap mt-4 p-2 border-y border-dashed border-strong">
        <div className="flex items-center gap-2 flex-1 min-w-xs">
          <Search size={14} className="text-muted" />
          <input 
            type="search" 
            className="filter-input w-full bg-transparent text-primary" 
            placeholder="Search SPP zones..." 
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
        </div>
        
        <div className="flex items-center gap-4 wrap">
          <div className="flex items-center gap-1">
            <ArrowUpDown size={12} className="text-muted" />
            <select 
              className="sort-select bg-transparent text-secondary bold cursor-pointer"
              value={sortBy}
              onChange={e => setSortBy(e.target.value as any)}
            >
              <option value="composite">Sort by: Composite Score</option>
              <option value="risk">Sort by: Lowest Risk</option>
              <option value="wind">Sort by: Highest Wind Absorption</option>
              <option value="carbon">Sort by: Deepest Decarbonization</option>
            </select>
          </div>

          <div className="view-segmented-control flex p-0-5 bg-strong radius-4">
            <button 
              className={`view-btn flex items-center gap-1 py-1 px-2 border-0 radius-3 cursor-pointer text-xs bold ${viewMode === 'table' ? 'bg-panel-bg text-primary' : 'bg-transparent text-secondary'}`}
              onClick={() => setViewMode('table')}
              title="Table View"
            >
              <LayoutGrid size={12} />
              <span>Table</span>
            </button>
            <button 
              className={`view-btn flex items-center gap-1 py-1 px-2 border-0 radius-3 cursor-pointer text-xs bold ${viewMode === 'chart' ? 'bg-panel-bg text-primary' : 'bg-transparent text-secondary'}`}
              onClick={() => setViewMode('chart')}
              title="Chart View"
            >
              <BarChart3 size={12} />
              <span>Chart</span>
            </button>
          </div>
        </div>
      </div>

      {viewMode === 'table' ? (
        <div className="leaderboard-table-wrap mt-4" tabIndex={0} aria-label="Spatial rankings list">
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
              {sortedRankings.map(item => {
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
      ) : (
        /* Visual Chart View: Horizontal BarChart comparing the scores cleanly */
        <div className="leaderboard-chart-wrap mt-6 p-4 border border-strong radius-6">
          <h4 className="flex items-center gap-2 mb-4 font-sans text-secondary text-sm bold">
            <BarChart3 size={14} className="text-teal" />
            Composite Suitability Ranking Comparison
          </h4>
          <ResponsiveContainer width="100%" height={Math.max(200, chartData.length * 40)}>
            <BarChart data={chartData} layout="vertical" margin={{ top: 5, right: 30, left: 10, bottom: 5 }}>
              <XAxis type="number" domain={[0, 100]} stroke="var(--border-strong)" />
              <YAxis dataKey="name" type="category" stroke="var(--border-strong)" width={120} tickStyle={{ fill: 'var(--text-secondary)', fontSize: 11 }} />
              <Tooltip 
                contentStyle={{ background: 'var(--panel-bg)', borderColor: 'var(--border-strong)', borderRadius: 4 }} 
                labelStyle={{ color: 'var(--text-primary)', fontWeight: 'bold' }}
              />
              <Bar dataKey="Composite Score" radius={[0, 4, 4, 0]} barSize={16}>
                {chartData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={index === 0 ? 'var(--signal)' : 'var(--teal)'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="field-note mt-4 text-center">First-ranked zone (highlighted in custom <span className="text-signal bold">Signal</span> orange) has the optimal mathematical blend of low curtailment risk and renewable potential.</p>
        </div>
      )}
    </section>
  )
}
