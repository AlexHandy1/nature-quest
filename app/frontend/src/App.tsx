import { useEffect } from 'react'
import InterestForm from './components/InterestForm'
import ConsentBanner from './components/ConsentBanner'
import { initPostHog } from './lib/posthog'

function App() {
  useEffect(() => {
    initPostHog()
  }, [])

  return (
    <main>
      <h1>Nature Quest</h1>
      <p>
        Coming soon — an AI agent that turns requests like "show me some
        plants and birds here" into personalised, narrated nature walks
        grounded in real biodiversity data.
      </p>
      <InterestForm />
      <ConsentBanner />
    </main>
  )
}

export default App
