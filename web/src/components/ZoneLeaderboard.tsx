import React, { useEffect, useState, useMemo } from 'react'
import { Award, Leaf, Wind, ShieldAlert, ChevronDown, ChevronUp, ClipboardList, Info } from 'lucide-react'
import { apiUrl } from '../api/client'
import type { Source, SourcedValue } from '../model'
import { Sourced, SourcedTick } from './Sourced'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

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
  status?: 'available' | 'unavailable'
  excluded_locations?: { location_id: string; reasons: string[]; source: Source }[]
  available_wind_evidence?: {
    location_id: string; reference_location_id: string
    period_start_utc: string; period_end_exclusive_utc: string
    proxy_hours: SourcedValue; evaluable_hours: SourcedValue; unknown_hours: SourcedValue
  }[]
}

const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

export function ZoneLeaderboard() {
  const [data, setData] = useState<ZoneRankingsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState<'composite' | 'risk' | 'wind' | 'carbon'>('composite')
  const [viewMode, setViewMode] = useState<'table' | 'chart'>('table')
  const matches = (id: string) => id.toLowerCase().includes(searchQuery.trim().toLowerCase())
  const sortedRankings = useMemo(() => {
    if (!data || data.status === 'unavailable') return []
    return data.rankings.filter(row => row.location_id.toLowerCase().includes(searchQuery.trim().toLowerCase()))
      .filter(row => !data.excluded_locations?.some(excluded => excluded.location_id === row.location_id))
      .sort((a, b) => sortBy === 'risk' ? a.avg_p90_risk_hours - b.avg_p90_risk_hours
        : sortBy === 'wind' ? b.wind_absorption_mwh_per_year - a.wind_absorption_mwh_per_year
          : sortBy === 'carbon' ? b.carbon_absorbed_tonnes_per_year - a.carbon_absorbed_tonnes_per_year
            : b.composite_score - a.composite_score)
  }, [data, searchQuery, sortBy])
  const windRows = (data?.available_wind_evidence ?? []).filter(row => matches(row.location_id))
    .sort((a, b) => sortBy === 'wind' ? b.proxy_hours.value - a.proxy_hours.value : a.location_id.localeCompare(b.location_id))
  const chartSource: Source = { source_type: 'assumption', ref: 'api://zone-rankings; display of supplied evidence only, no imputation' }
  const chartRows: { name: string; value: number; source: Source }[] = data?.status === 'unavailable'
    ? windRows.map(row => ({ name: row.location_id, value: row.proxy_hours.value, source: row.proxy_hours }))
    : sortedRankings.map(row => ({ name: row.location_id,
      value: sortBy === 'risk' ? row.avg_p90_risk_hours : sortBy === 'wind' ? row.wind_absorption_mwh_per_year : sortBy === 'carbon' ? row.carbon_absorbed_tonnes_per_year : row.composite_score,
      source: { source_type: 'assumption' as const, ref: `${row.wind_source_ref}; published ${sortBy} comparison; api://zone-rankings` } }))
  const chartUnit = data?.status === 'unavailable' ? 'screened hours in supplied observation period'
    : sortBy === 'risk' ? 'p90 modeled exposure · h/year' : sortBy === 'wind' ? 'wind absorption · MWh/year' : sortBy === 'carbon' ? 'supplied carbon estimate · tonnes/year' : 'published composite score'

  useEffect(() => {
    let active = true
    async function fetchRankings() {
      try {
        const response = await fetch(apiUrl('/api/zone-rankings'))
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`)
        }
        const json = await response.json()
        if (active) setData(json)
      } catch (err: any) {
        if (active) setError(err.message || 'Failed to fetch rankings')
      } finally {
        if (active) setLoading(false)
      }
    }
    fetchRankings()
    return () => { active = false }
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

      <div className="leaderboard-controls">
        <label>Search zones<input type="search" aria-label="Search SPP zones" placeholder="Search SPP zones..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)} /></label>
        <label>Sort evidence<select aria-label="Sort leaderboard" value={sortBy} onChange={e => setSortBy(e.target.value as typeof sortBy)}>
          <option value="composite">Composite score</option><option value="risk">Lowest p90 modeled exposure</option><option value="wind">Highest wind evidence</option><option value="carbon">Highest supplied carbon estimate</option>
        </select></label>
        <div className="segmented-control" aria-label="Leaderboard view"><button aria-pressed={viewMode === 'table'} onClick={() => setViewMode('table')}>Table</button><button aria-pressed={viewMode === 'chart'} onClick={() => setViewMode('chart')}>Chart</button></div>
      </div>
      {data.status === 'unavailable' && sortBy !== 'wind' && <p className="field-note">Composite, annual risk and carbon rankings are unavailable. Available wind references are listed alphabetically; choose wind to sort by screened hours.</p>}
      {viewMode === 'chart' && <div className="leaderboard-chart-wrap">
        <h3>{data.status === 'unavailable' ? 'Wind-screening evidence — not composite ranks' : 'Published zone comparison'}</h3><p className="field-note">{chartUnit}</p>
        {chartRows.length ? <><ResponsiveContainer width="100%" height={Math.max(220, chartRows.length * 42)}>
          <BarChart data={chartRows} layout="vertical" margin={{ top: 10, right: 30, left: 20, bottom: 15 }}>
            <XAxis type="number" tick={<SourcedTick source={chartSource} />} /><YAxis type="category" dataKey="name" width={110} tick={<SourcedTick source={chartSource} />} />
            <Tooltip content={({ active, payload }) => active && payload?.length ? <div className="chart-tooltip"><Sourced value={payload[0].payload.value} source={payload[0].payload.source} /> {chartUnit}</div> : null} />
            <Bar dataKey="value" fill="var(--text-muted)" barSize={16} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer><details><summary>Inspect supplied chart values</summary><ul>{chartRows.map(row => <li key={row.name}>{row.name}: <Sourced value={row.value} source={row.source} /> {chartUnit}</li>)}</ul></details></> : <p>No matching evidence to plot.</p>}
      </div>}

      {data.status === 'unavailable' && <div className="leaderboard-intro" role="status">
        <h3>Composite ranking unavailable</h3>
        <p>No zones meet the evidence requirements for this composite. Missing inputs are not filled with assumed scores.</p>
      </div>}
      {viewMode === 'table' && !!data.available_wind_evidence?.length && <div className="leaderboard-table-wrap">
        <table className="leaderboard-table" aria-label="Wind evidence">
          <thead><tr><th>Zone reference</th><th>Observed period (UTC)</th><th>High-wind / nonpositive-price hours</th><th>Evaluable hours</th><th>Unknown hours</th></tr></thead>
          <tbody>{windRows.map(item => <tr key={item.location_id}>
            <td>{item.location_id} · {item.reference_location_id}</td>
            <td><Sourced value={`${item.period_start_utc.slice(0, 10)} to ${item.period_end_exclusive_utc.slice(0, 10)} (end exclusive)`} source={item.proxy_hours} /></td>
            <td><Sourced value={item.proxy_hours.value} source={item.proxy_hours} animate={false} /></td>
            <td><Sourced value={item.evaluable_hours.value} source={item.evaluable_hours} animate={false} /></td>
            <td><Sourced value={item.unknown_hours.value} source={item.unknown_hours} animate={false} /></td>
          </tr>)}</tbody>
        </table>
        <p className="field-note">These are documented within-zone settlement-point references, not measured recoverable wind or site deliverability. Unknown hours are not filled; the evidence retains its timing and screening assumptions.</p>
      </div>}
      {!!data.excluded_locations?.length && <details className="leaderboard-intro">
        <summary>Excluded locations and missing evidence</summary>
        <ul>{data.excluded_locations.map(item => <li key={item.location_id}>
          <Sourced value={item.location_id} source={item.source} />: {item.reasons.join(' ')}
        </li>)}</ul>
      </details>}

      {viewMode === 'table' && data.status !== 'unavailable' && <div className="leaderboard-table-wrap" tabIndex={0} aria-label="Spatial rankings list">
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
                ? item.location_id.replace('spp-', '').replace('-demo', '').toUpperCase() + ' (Scenario)'
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
                                <dd className="text-muted">Supplied ranking evidence only; associated wind operating emissions are not verified avoided emissions.</dd>
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
      </div>}
    </section>
  )
}
