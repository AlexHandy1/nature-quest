import { useEffect } from 'react'
import MapView from './components/MapView'
import ConsentBanner from './components/ConsentBanner'
import { initPostHog } from './lib/posthog'

function App() {
  useEffect(() => {
    initPostHog()
  }, [])

  return (
    <main>
      <MapView />
      <ConsentBanner />
    </main>
  )
}

export default App
