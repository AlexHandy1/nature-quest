import posthog from 'posthog-js'

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
