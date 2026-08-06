import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect, beforeEach } from 'vitest'
import QueryForm from './QueryForm'
import { getDistinctId, hasConsent, trackEvent } from '../lib/posthog'

vi.mock('../lib/posthog', () => ({
  getDistinctId: vi.fn(() => 'anon-123'),
  hasConsent: vi.fn(() => false),
  trackEvent: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getDistinctId).mockReturnValue('anon-123')
  vi.mocked(hasConsent).mockReturnValue(false)
})

function jsonResponse(status: number, body: unknown) {
  return { status, json: () => Promise.resolve(body) }
}

async function submit(query = 'birds') {
  const user = userEvent.setup()
  await user.type(
    screen.getByLabelText('What would you want to see on a walk?'),
    query
  )
  await user.click(screen.getByRole('button', { name: /show me/i }))
}

test('renders the label, input, and submit button', () => {
  render(<QueryForm />)

  expect(
    screen.getByLabelText('What would you want to see on a walk?')
  ).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /show me/i })).toBeInTheDocument()
})

test('submitting POSTs to /api/query with query, distinctId, and consent', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse(200, { status: 'unresolved', message: 'no match' })
  )
  vi.stubGlobal('fetch', fetchMock)
  vi.mocked(hasConsent).mockReturnValue(true)

  render(<QueryForm />)
  await submit('birds')

  expect(fetchMock).toHaveBeenCalledWith('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: 'birds', distinctId: 'anon-123', consent: true }),
  })
})

test('disables the submit button and shows a loading message while in flight', async () => {
  let resolveFetch: (value: unknown) => void = () => {}
  vi.stubGlobal(
    'fetch',
    vi.fn().mockReturnValue(new Promise((resolve) => (resolveFetch = resolve)))
  )

  render(<QueryForm />)
  await submit('birds')

  expect(screen.getByRole('button', { name: /searching/i })).toBeDisabled()
  expect(screen.getByText(/searching retiro park/i)).toBeInTheDocument()

  resolveFetch(jsonResponse(200, { status: 'unresolved', message: 'no match' }))
})

test('shows the species list on a resolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, {
        status: 'resolved',
        taxonRank: 'class',
        taxonValue: 'Aves',
        species: [
          { species: 'Turdus merula', count: 42, kingdom: 'Animalia' },
          { species: 'Pica pica', count: 30, kingdom: 'Animalia' },
        ],
        message: 'Early preview message',
      })
    )
  )

  render(<QueryForm />)
  await submit('birds')

  expect(await screen.findByText('Early preview message')).toBeInTheDocument()
  expect(screen.getByText(/Turdus merula/)).toBeInTheDocument()
  expect(screen.getByText(/42/)).toBeInTheDocument()
  expect(screen.getByText(/Pica pica/)).toBeInTheDocument()
})

test('shows the message with no species list on an unresolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, { status: 'unresolved', message: "couldn't match that" })
    )
  )

  render(<QueryForm />)
  await submit('surprise me')

  expect(await screen.findByText("couldn't match that")).toBeInTheDocument()
  expect(screen.queryByRole('list')).not.toBeInTheDocument()
})

test('shows the message on a no_results outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, {
        status: 'no_results',
        taxonRank: 'class',
        taxonValue: 'Aves',
        message: 'nothing found right now',
      })
    )
  )

  render(<QueryForm />)
  await submit('birds')

  expect(await screen.findByText('nothing found right now')).toBeInTheDocument()
})

test('shows the message on a gbif_unavailable (502) outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(502, { status: 'gbif_unavailable', message: 'trouble reaching nature data' })
    )
  )

  render(<QueryForm />)
  await submit('birds')

  expect(await screen.findByText('trouble reaching nature data')).toBeInTheDocument()
})

test('shows distinct copy for a rate_limited (429) outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(429, { error: 'rate_limited', message: 'slow down, try again shortly' })
    )
  )

  render(<QueryForm />)
  await submit('birds')

  expect(await screen.findByText('slow down, try again shortly')).toBeInTheDocument()
})

test('shows distinct copy for a daily_limit_reached (429) outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(429, {
        error: 'daily_limit_reached',
        message: "today's limit reached, try tomorrow",
      })
    )
  )

  render(<QueryForm />)
  await submit('birds')

  expect(await screen.findByText("today's limit reached, try tomorrow")).toBeInTheDocument()
})

test('replaces the form with the result so it cannot be resubmitted', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, { status: 'unresolved', message: 'no match' })
    )
  )

  render(<QueryForm />)
  await submit('birds')
  await screen.findByText('no match')

  expect(
    screen.queryByLabelText('What would you want to see on a walk?')
  ).not.toBeInTheDocument()
})

test('tracks query_submitted on submit and query_outcome with the resolved status on response', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, { status: 'resolved', species: [], message: 'ok' })
    )
  )

  render(<QueryForm />)
  await submit('birds')
  await screen.findByText('ok')

  expect(trackEvent).toHaveBeenCalledWith('query_submitted')
  expect(trackEvent).toHaveBeenCalledWith('query_outcome', { status: 'resolved' })
})

test('tracks query_outcome with the error value for a 429 response', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(429, { error: 'rate_limited', message: 'slow down' })
    )
  )

  render(<QueryForm />)
  await submit('birds')
  await screen.findByText('slow down')

  expect(trackEvent).toHaveBeenCalledWith('query_outcome', { status: 'rate_limited' })
})

test('submits successfully when consent has not been granted', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse(200, { status: 'unresolved', message: 'no match' })
  )
  vi.stubGlobal('fetch', fetchMock)
  vi.mocked(hasConsent).mockReturnValue(false)

  render(<QueryForm />)
  await submit('birds')

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/query',
    expect.objectContaining({
      body: JSON.stringify({ query: 'birds', distinctId: 'anon-123', consent: false }),
    })
  )
  expect(await screen.findByText('no match')).toBeInTheDocument()
})
