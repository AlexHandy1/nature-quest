export type Outcome =
  | 'resolved'
  | 'unresolved'
  | 'no_results'
  | 'gbif_unavailable'
  | 'rate_limited'
  | 'daily_limit_reached'

export type Result = {
  outcome: Outcome
  message: string
}

const VARIANT_BY_OUTCOME: Record<Outcome, 'success' | 'neutral' | 'error'> = {
  resolved: 'success',
  unresolved: 'neutral',
  no_results: 'neutral',
  gbif_unavailable: 'error',
  rate_limited: 'error',
  daily_limit_reached: 'error',
}

type QueryPanelProps = {
  query: string
  onQueryChange: (value: string) => void
  loading: boolean
  result: Result | null
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void
  docked: boolean
}

function QueryPanel({ query, onQueryChange, loading, result, onSubmit, docked }: QueryPanelProps) {
  return (
    <div className={`query-panel${docked ? ' query-panel--docked' : ''}`}>
      <h1>Nature Quest</h1>
      {!docked && (
        <p className="query-panel__intro">
          Tell us what you'd like to see on a walk through Retiro Park, Madrid
          — e.g. "show me some birds" or "birds and plants". We'll find real,
          recently-observed species nearby and plot a walking route between
          them on the map.
        </p>
      )}
      <form onSubmit={onSubmit}>
        <label htmlFor="query">What would you want to see on a walk?</label>
        <input
          id="query"
          name="query"
          placeholder='e.g. "show me some birds" or "show me some plants"'
          value={query}
          disabled={loading}
          onChange={(event) => onQueryChange(event.target.value)}
        />
        <button type="submit" disabled={loading}>
          {loading ? 'Searching…' : 'Show me'}
        </button>
      </form>
      {loading && (
        <p className="form-message">
          Searching Retiro Park for you — this can take a few seconds.
        </p>
      )}
      {result && !loading && (
        <p className={`form-message form-message--${VARIANT_BY_OUTCOME[result.outcome]}`}>
          {result.message}
        </p>
      )}
    </div>
  )
}

export default QueryPanel
