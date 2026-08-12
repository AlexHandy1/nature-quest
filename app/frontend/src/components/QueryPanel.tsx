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
}

function QueryPanel({ query, onQueryChange, loading, result, onSubmit }: QueryPanelProps) {
  return (
    <>
      <form className="nav-bar__query" onSubmit={onSubmit}>
        <label htmlFor="query" className="sr-only">
          What would you want to see on a walk?
        </label>
        <input
          id="query"
          name="query"
          placeholder='e.g. "birds" or "birds and plants"'
          value={query}
          disabled={loading}
          onChange={(event) => onQueryChange(event.target.value)}
        />
        <button type="submit" disabled={loading}>
          {loading ? 'Searching…' : 'Create walk'}
        </button>
      </form>
      {loading && (
        <p className="form-message nav-bar__message">Searching — this can take a few seconds.</p>
      )}
      {result && !loading && (
        <p className={`form-message nav-bar__message form-message--${VARIANT_BY_OUTCOME[result.outcome]}`}>
          {result.message}
        </p>
      )}
    </>
  )
}

export default QueryPanel
