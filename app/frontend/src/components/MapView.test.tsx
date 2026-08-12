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
  Polygon: ({ positions }: { positions: [number, number][] }) => (
    <div data-testid="area-polygon" data-points={positions.length} />
  ),
  Marker: ({ position }: { position: [number, number] }) => (
    <div data-testid="marker" data-lat={position[0]} data-lon={position[1]} />
  ),
  Polyline: ({ positions }: { positions: [number, number][] }) => (
    <div data-testid="polyline" data-points={positions.length} />
  ),
  useMap: () => ({ setView: vi.fn(), getZoom: () => 15 }),
}))

const CUSTOM_POLYGON = 'POLYGON((-3.87 40.90,-3.86 40.90,-3.86 40.89,-3.87 40.90))'

vi.mock('./DrawAreaControl', () => ({
  default: ({ onConfirm }: { onConfirm: (polygon: string, center: [number, number]) => void }) => (
    <div data-testid="draw-area-control">
      <button type="button" onClick={() => onConfirm(CUSTOM_POLYGON, [40.895, -3.865])}>
        Confirm (mock)
      </button>
    </div>
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
  await user.click(screen.getByRole('button', { name: /create walk/i }))
}

test('renders the map, area control, and query panel immediately in fixed mode', () => {
  render(<MapView />)

  expect(screen.getByTestId('map-container')).toBeInTheDocument()
  expect(screen.getByText(/exploring: retiro park/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /draw your own area/i })).toBeInTheDocument()
  expect(screen.getByLabelText('What would you want to see on a walk?')).toBeInTheDocument()
})

test('clicking "Draw your own area" shows the draw control', async () => {
  const user = userEvent.setup()
  render(<MapView />)

  await user.click(screen.getByRole('button', { name: /draw your own area/i }))

  expect(screen.getByTestId('draw-area-control')).toBeInTheDocument()
})

test('confirming a drawn polygon updates the area control and submits that polygon', async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse(200, { status: 'unresolved', message: 'no match' })
  )
  vi.stubGlobal('fetch', fetchMock)
  render(<MapView />)

  await user.click(screen.getByRole('button', { name: /draw your own area/i }))
  await user.click(screen.getByRole('button', { name: /confirm \(mock\)/i }))

  expect(screen.getByText(/custom area/i)).toBeInTheDocument()

  await submit('plants')

  const [, options] = fetchMock.mock.calls[0]
  const sentBody = JSON.parse(options.body)
  expect(sentBody.polygon).toBe(CUSTOM_POLYGON)
})

test('"Redraw area" reopens the draw control without losing the query panel afterward', async () => {
  const user = userEvent.setup()
  render(<MapView />)
  await user.click(screen.getByRole('button', { name: /draw your own area/i }))
  await user.click(screen.getByRole('button', { name: /confirm \(mock\)/i }))

  await user.click(screen.getByRole('button', { name: /redraw area/i }))

  expect(screen.getByTestId('draw-area-control')).toBeInTheDocument()
})

test('"Explore Retiro Park" from draw mode switches back to fixed mode', async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse(200, { status: 'unresolved', message: 'no match' })
  )
  vi.stubGlobal('fetch', fetchMock)
  render(<MapView />)
  await user.click(screen.getByRole('button', { name: /draw your own area/i }))
  await user.click(screen.getByRole('button', { name: /confirm \(mock\)/i }))

  await user.click(screen.getByRole('button', { name: /explore retiro park/i }))

  expect(screen.getByRole('button', { name: /draw your own area/i })).toBeInTheDocument()

  await submit('plants')
  const [, options] = fetchMock.mock.calls[0]
  const sentBody = JSON.parse(options.body)
  expect(sentBody.polygon).not.toBe(CUSTOM_POLYGON)
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

test('shows the outcome message on a resolved outcome', async () => {
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
})

test('shows no markers on an unresolved outcome', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(jsonResponse(200, { status: 'unresolved', message: "couldn't match that" }))
  )

  render(<MapView />)
  await submit('surprise me')

  await screen.findByText("couldn't match that")
  expect(screen.queryByTestId('marker')).not.toBeInTheDocument()
})

test('posts query, distinctId, and consent to /api/query', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse(200, { status: 'unresolved', message: 'no match' })
  )
  vi.stubGlobal('fetch', fetchMock)
  vi.mocked(hasConsent).mockReturnValue(true)

  render(<MapView />)
  await submit('birds')

  const [, options] = fetchMock.mock.calls[0]
  const sentBody = JSON.parse(options.body)
  expect(sentBody.query).toBe('birds')
  expect(sentBody.distinctId).toBe('anon-123')
  expect(sentBody.consent).toBe(true)
  expect(typeof sentBody.polygon).toBe('string')
  expect(sentBody.polygon.length).toBeGreaterThan(0)
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
  expect(screen.getByRole('button', { name: /create walk/i })).not.toBeDisabled()
})
