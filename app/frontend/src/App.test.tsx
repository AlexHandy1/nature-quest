import { render, screen } from '@testing-library/react'
import { vi, beforeEach } from 'vitest'
import App from './App'
import { initPostHog } from './lib/posthog'

vi.mock('./lib/posthog', () => ({
  initPostHog: vi.fn(),
  optIn: vi.fn(),
  optOut: vi.fn(),
  getDistinctId: vi.fn(() => 'anon-test'),
  hasConsent: vi.fn(() => false),
  trackEvent: vi.fn(),
  CONSENT_KEY: 'analytics-consent',
}))

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TileLayer: () => null,
  Polygon: () => null,
  Marker: () => null,
  Polyline: () => null,
  useMap: () => ({ setView: vi.fn(), getZoom: () => 15 }),
}))

beforeEach(() => {
  window.localStorage.clear()
  vi.clearAllMocks()
})

test('renders the Nature Quest heading', () => {
  render(<App />)

  expect(screen.getByRole('heading', { name: /nature quest/i })).toBeInTheDocument()
})

test('renders the query panel and the consent banner', () => {
  render(<App />)

  expect(
    screen.getByLabelText('What would you want to see on a walk?')
  ).toBeInTheDocument()
  expect(
    screen.getByText(/we use privacy-friendly analytics/i)
  ).toBeInTheDocument()
})

test('initializes PostHog once on mount', () => {
  render(<App />)

  expect(initPostHog).toHaveBeenCalledTimes(1)
})
