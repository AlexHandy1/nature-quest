import { useState } from 'react'
import { getDistinctId, hasConsent } from '../lib/posthog'

type Species = { species: string; count: number }

type Outcome =
  | 'resolved'
  | 'unresolved'
  | 'no_results'
  | 'gbif_unavailable'
  | 'rate_limited'
  | 'daily_limit_reached'

type Result = {
  outcome: Outcome
  message: string
  species?: Species[]
}

const VARIANT_BY_OUTCOME: Record<Outcome, 'success' | 'neutral' | 'error'> = {
  resolved: 'success',
  unresolved: 'neutral',
  no_results: 'neutral',
  gbif_unavailable: 'error',
  rate_limited: 'error',
  daily_limit_reached: 'error',
}

function QueryForm() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    const response = await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        distinctId: getDistinctId(),
        consent: hasConsent(),
      }),
    })
    const body = await response.json()
    setLoading(false)
    setResult({
      outcome: body.status ?? body.error,
      message: body.message,
      species: body.species,
    })
  }

  if (result) {
    return (
      <p className={`form-message form-message--${VARIANT_BY_OUTCOME[result.outcome]}`}>
        {result.message}
        {result.species && (
          <ul>
            {result.species.map((item) => (
              <li key={item.species}>
                {item.species} ({item.count})
              </li>
            ))}
          </ul>
        )}
      </p>
    )
  }

  return (
    <form onSubmit={handleSubmit}>
      <label htmlFor="query">What would you want to see on a walk?</label>
      <input
        id="query"
        name="query"
        placeholder='e.g. "show me some birds near here" or "something rare"'
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <button type="submit" disabled={loading}>
        {loading ? 'Searching…' : 'Show me'}
      </button>
      {loading && <p className="form-message">Searching Retiro Park for you…</p>}
    </form>
  )
}

export default QueryForm
