import posthog from 'posthog-js'

export const CONSENT_KEY = 'analytics-consent'

export function initPostHog() {
  posthog.init(import.meta.env.VITE_POSTHOG_KEY, {
    api_host: 'https://eu.i.posthog.com',
    opt_out_capturing_by_default: true,
  })
}

export function optIn() {
  posthog.opt_in_capturing()
}

export function optOut() {
  posthog.opt_out_capturing()
}

export function getDistinctId(): string {
  return posthog.get_distinct_id()
}

export function hasConsent(): boolean {
  return window.localStorage.getItem(CONSENT_KEY) === 'accepted'
}

export function trackEvent(event: string, properties?: Record<string, unknown>) {
  posthog.capture(event, properties)
}
