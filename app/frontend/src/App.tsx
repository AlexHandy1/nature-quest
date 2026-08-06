import { useEffect } from 'react'
import QueryForm from './components/QueryForm'
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
        grounded in real biodiversity data. Right now, you can query what
        species are in Retiro Park, Madrid, and get back a real species
        list — many more features and full global search are coming soon.
      </p>
      <QueryForm />
      <ConsentBanner />
    </main>
  )
}

export default App
