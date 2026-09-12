import type { SourcedValue } from './types'

/** UI fixture only. This is not part of the canonical /api/estimate response. */
export interface TransparencyDiagnostics {
  status: 'mock'
  target: 'system_stress'
  brier_score: SourcedValue
  naive_brier_score: SourcedValue
  reliability_curve: Array<{
    mean_predicted: SourcedValue
    observed_fraction: SourcedValue
  }>
}

const placeholder = (value: number, field: string): SourcedValue => ({
  value,
  source_type: 'assumption',
  ref: `mock://transparency/diagnostics/${field}; authored UI placeholder, no held-out evaluation performed`,
})

/** Authored plotting examples, not measurements or a fitted calibration curve. */
export const mockTransparencyDiagnostics: TransparencyDiagnostics = {
  status: 'mock',
  target: 'system_stress',
  brier_score: placeholder(0.2, 'brier_score'),
  naive_brier_score: placeholder(0.25, 'naive_brier_score'),
  reliability_curve: [
    [0.1, 0.2],
    [0.3, 0.35],
    [0.5, 0.4],
    [0.7, 0.65],
    [0.9, 0.8],
  ].map(([meanPredicted, observedFraction], index) => ({
    mean_predicted: placeholder(meanPredicted, `reliability_curve/${index}/mean_predicted`),
    observed_fraction: placeholder(observedFraction, `reliability_curve/${index}/observed_fraction`),
  })),
}
