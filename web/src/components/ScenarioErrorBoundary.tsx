import { Component, type ErrorInfo, type ReactNode } from 'react'

/** A failed scenario must show an error rather than stale results or a blank page. */
export class ScenarioErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) { return { error } }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Scenario rendering failed', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return <main className="app-shell"><section className="panel" role="alert">
      <div className="panel-heading"><h1>Analysis could not be displayed</h1></div>
      <p>The current calculation failed. No results are shown for this scenario.</p>
      <p>{this.state.error.message}</p>
      <button className="button" onClick={() => this.setState({ error: null })}>Reset analysis</button>
    </section></main>
  }
}
