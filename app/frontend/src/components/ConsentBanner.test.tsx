import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { test, expect, vi, beforeEach } from 'vitest'
import ConsentBanner from './ConsentBanner'
import { optIn, optOut } from '../lib/posthog'

vi.mock('../lib/posthog', () => ({
  optIn: vi.fn(),
  optOut: vi.fn(),
  CONSENT_KEY: 'analytics-consent',
}))

beforeEach(() => {
  window.localStorage.clear()
  vi.clearAllMocks()
})

test('renders the consent disclosure and accept/reject buttons on first visit', () => {
  render(<ConsentBanner />)

  expect(
    screen.getByText(/we use privacy-friendly analytics/i)
  ).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /accept/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument()
})

test('accepting calls optIn and persists the choice', async () => {
  const user = userEvent.setup()
  render(<ConsentBanner />)

  await user.click(screen.getByRole('button', { name: /accept/i }))

  expect(optIn).toHaveBeenCalled()
  expect(window.localStorage.getItem('analytics-consent')).toBe('accepted')
})

test('rejecting calls optOut and persists the choice', async () => {
  const user = userEvent.setup()
  render(<ConsentBanner />)

  await user.click(screen.getByRole('button', { name: /reject/i }))

  expect(optOut).toHaveBeenCalled()
  expect(window.localStorage.getItem('analytics-consent')).toBe('rejected')
})

test('does not render again once a choice has already been persisted', () => {
  window.localStorage.setItem('analytics-consent', 'accepted')

  render(<ConsentBanner />)

  expect(screen.queryByRole('button', { name: /accept/i })).not.toBeInTheDocument()
})
