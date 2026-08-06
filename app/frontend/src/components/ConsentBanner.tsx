import { useState } from 'react'
import { optIn, optOut, CONSENT_KEY } from '../lib/posthog'

function ConsentBanner() {
  const [choice, setChoice] = useState(() => window.localStorage.getItem(CONSENT_KEY))

  if (choice) {
    return null
  }

  function accept() {
    optIn()
    window.localStorage.setItem(CONSENT_KEY, 'accepted')
    setChoice('accepted')
  }

  function reject() {
    optOut()
    window.localStorage.setItem(CONSENT_KEY, 'rejected')
    setChoice('rejected')
  }

  return (
    <div className="consent-banner" role="region" aria-label="Cookie consent">
      <p>
        We use privacy-friendly analytics to see how people use this page. We
        never ask for or store your name or email.
      </p>
      <div>
        <button type="button" onClick={accept}>
          Accept
        </button>
        <button type="button" data-variant="reject" onClick={reject}>
          Reject
        </button>
      </div>
    </div>
  )
}

export default ConsentBanner
