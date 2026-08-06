import { vi, test, expect, beforeEach } from 'vitest'
import posthog from 'posthog-js'
import {
  initPostHog,
  optIn,
  optOut,
  getDistinctId,
  hasConsent,
  trackEvent,
  CONSENT_KEY,
} from './posthog'

vi.mock('posthog-js', () => ({
  default: {
    init: vi.fn(),
    opt_in_capturing: vi.fn(),
    opt_out_capturing: vi.fn(),
    get_distinct_id: vi.fn(),
    capture: vi.fn(),
  },
}))

beforeEach(() => {
  window.localStorage.clear()
})

test('initPostHog initializes the SDK with capture opted out by default', () => {
  vi.stubEnv('VITE_POSTHOG_KEY', 'test-project-token')

  initPostHog()

  expect(posthog.init).toHaveBeenCalledWith(
    'test-project-token',
    expect.objectContaining({
      api_host: 'https://eu.i.posthog.com',
      opt_out_capturing_by_default: true,
    })
  )
})

test('optIn calls the SDK opt_in_capturing method', () => {
  optIn()

  expect(posthog.opt_in_capturing).toHaveBeenCalled()
})

test('optOut calls the SDK opt_out_capturing method', () => {
  optOut()

  expect(posthog.opt_out_capturing).toHaveBeenCalled()
})

test('getDistinctId returns the SDK current distinct id', () => {
  vi.mocked(posthog.get_distinct_id).mockReturnValue('anon-123')

  expect(getDistinctId()).toBe('anon-123')
})

test('hasConsent returns false when no consent choice has been made', () => {
  expect(hasConsent()).toBe(false)
})

test('hasConsent returns true when consent was accepted', () => {
  window.localStorage.setItem(CONSENT_KEY, 'accepted')

  expect(hasConsent()).toBe(true)
})

test('hasConsent returns false when consent was rejected', () => {
  window.localStorage.setItem(CONSENT_KEY, 'rejected')

  expect(hasConsent()).toBe(false)
})

test('trackEvent calls the SDK capture method with the event name and properties', () => {
  trackEvent('query_outcome', { status: 'resolved' })

  expect(posthog.capture).toHaveBeenCalledWith('query_outcome', { status: 'resolved' })
})

test('trackEvent works with no properties', () => {
  trackEvent('query_submitted')

  expect(posthog.capture).toHaveBeenCalledWith('query_submitted', undefined)
})
