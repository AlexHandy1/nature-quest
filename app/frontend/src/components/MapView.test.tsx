import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect, beforeEach } from 'vitest'
import MapView from './MapView'
import { hasConsent, trackEvent } from '../lib/posthog'

vi.mock('../lib/posthog', () => ({
  getDistinctId: vi.fn(() => 'anon-123'),
  hasConsent: vi.fn(() => false),
  trackEvent: vi.fn(),
}))

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="map-container">{children}</div>
  ),
  TileLayer: () => <div data-testid="tile-layer" />,
  Marker: ({ position }: { position: [number, number] }) => (
    <div data-testid="marker" data-lat={position[0]} data-lon={position[1]} />
  ),
  Polyline: ({ positions }: { positions: [number, number][] }) => (
    <div data-testid="polyline" data-points={positions.length} />
  ),
}))

beforeEach(() => {
  vi.clearAllMocks()
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

test('renders the map and the query panel', () => {
  render(<MapView />)

  expect(screen.getByTestId('map-container')).toBeInTheDocument()
  expect(
    screen.getByLabelText('What would you want to see on a walk?')
  ).toBeInTheDocument()
  expect(screen.getByText(/tell us what you'd like to see/i)).toBeInTheDocument()
})

test('plots numbered markers and a route line on a resolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, {
        status: 'resolved',
        species: [
          { species: 'Turdus merula', count: 42, kingdom: 'Animalia', hotspot_lat: 40.41, hotspot_lon: -3.68 },
          { species: 'Pica pica', count: 30, kingdom: 'Animalia', hotspot_lat: 40.42, hotspot_lon: -3.69 },
        ],
        message: 'Early preview message',
      })
    )
  )

  render(<MapView />)
  await submit('birds')

  const markers = await screen.findAllByTestId('marker')
  expect(markers).toHaveLength(2)
  expect(screen.getByTestId('polyline')).toHaveAttribute('data-points', '2')
})

test('shows the results panel with species names and counts on a resolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, {
        status: 'resolved',
        species: [
          { species: 'Turdus merula', count: 42, kingdom: 'Animalia', hotspot_lat: 40.41, hotspot_lon: -3.68 },
          { species: 'Pica pica', count: 30, kingdom: 'Animalia', hotspot_lat: 40.42, hotspot_lon: -3.69 },
        ],
        message: 'Early preview message',
      })
    )
  )

  render(<MapView />)
  await submit('birds')

  const items = await screen.findAllByRole('listitem')
  expect(items[0]).toHaveTextContent('Turdus merula')
  expect(items[0]).toHaveTextContent('42')
  expect(items[1]).toHaveTextContent('Pica pica')
})

test('docks the panel and hides the intro copy on a resolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, {
        status: 'resolved',
        species: [
          { species: 'Turdus merula', count: 42, kingdom: 'Animalia', hotspot_lat: 40.41, hotspot_lon: -3.68 },
        ],
        message: 'Early preview message',
      })
    )
  )

  render(<MapView />)
  await submit('birds')

  await screen.findByText('Early preview message')
  expect(screen.queryByText(/tell us what you'd like to see/i)).not.toBeInTheDocument()
})

test('shows no markers and keeps the panel expanded on an unresolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(jsonResponse(200, { status: 'unresolved', message: "couldn't match that" }))
  )

  render(<MapView />)
  await submit('surprise me')

  await screen.findByText("couldn't match that")
  expect(screen.queryByTestId('marker')).not.toBeInTheDocument()
  expect(screen.getByText(/tell us what you'd like to see/i)).toBeInTheDocument()
})

test('posts query, distinctId, and consent to /api/query', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse(200, { status: 'unresolved', message: 'no match' })
  )
  vi.stubGlobal('fetch', fetchMock)
  vi.mocked(hasConsent).mockReturnValue(true)

  render(<MapView />)
  await submit('birds')

  expect(fetchMock).toHaveBeenCalledWith('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: 'birds', distinctId: 'anon-123', consent: true }),
  })
})

test('tracks query_submitted and query_outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(jsonResponse(200, { status: 'resolved', species: [], message: 'ok' }))
  )

  render(<MapView />)
  await submit('birds')

  await screen.findByText('ok')
  expect(trackEvent).toHaveBeenCalledWith('query_submitted')
  expect(trackEvent).toHaveBeenCalledWith('query_outcome', { status: 'resolved' })
})

test('allows submitting a new query after a resolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, { status: 'resolved', species: [], message: 'ok' })
    )
  )

  render(<MapView />)
  await submit('birds')
  await screen.findByText('ok')

  expect(screen.getByLabelText('What would you want to see on a walk?')).not.toBeDisabled()
  expect(screen.getByRole('button', { name: /show me/i })).not.toBeDisabled()
})
