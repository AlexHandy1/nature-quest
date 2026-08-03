import { useState } from 'react'

type SubmissionState = 'idle' | 'success' | 'error'

function InterestForm() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<SubmissionState>('idle')

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const response = await fetch('/api/interest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    })
    setStatus(response.ok ? 'success' : 'error')
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
      <button type="submit">Count me in</button>
      {status === 'success' && <p>You're on the list!</p>}
      {status === 'error' && <p>Something went wrong — please try again.</p>}
    </form>
  )
}

export default InterestForm
