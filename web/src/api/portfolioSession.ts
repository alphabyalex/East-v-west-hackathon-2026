// One session shared by the scenario save control and the portfolio display.
const key = 'fluxline_portfolio_session'
let memorySession: string | null = null

export function readPortfolioSession(): string | null {
  try { return sessionStorage.getItem(key) } catch { return memorySession }
}

export function writePortfolioSession(id: string): void {
  memorySession = id
  try { sessionStorage.setItem(key, id) } catch { /* Retain across tabs in this page. */ }
}
