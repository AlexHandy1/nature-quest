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

beforeEach(() => {
  window.localStorage.clear()
  vi.clearAllMocks()
})

test('renders the Nature Quest heading', () => {
  render(<App />)

  expect(screen.getByRole('heading', { name: 'Nature Quest' })).toBeInTheDocument()
})

test('renders the query form and the consent banner', () => {
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
