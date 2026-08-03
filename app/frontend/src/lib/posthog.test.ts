import { vi, test, expect } from 'vitest'
import posthog from 'posthog-js'
import { initPostHog, optIn, optOut } from './posthog'

vi.mock('posthog-js', () => ({
  default: { init: vi.fn(), opt_in_capturing: vi.fn(), opt_out_capturing: vi.fn() },
}))

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
