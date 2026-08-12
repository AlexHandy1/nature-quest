import { useState } from 'react'

type GeolocationPromptProps = {
  onLocated: (lat: number, lon: number) => void
}

const FALLBACK_MESSAGE = "Couldn't get your location — pan and zoom manually."

function GeolocationPrompt({ onLocated }: GeolocationPromptProps) {
  const [error, setError] = useState(!navigator.geolocation)

  function handleClick() {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        onLocated(position.coords.latitude, position.coords.longitude)
      },
      () => {
        setError(true)
      }
    )
  }

  return (
    <div className="geolocation-prompt">
      <p className="geolocation-prompt__copy">
        This only centers the map on your position — it's never sent to the
        server or stored, and is separate from analytics consent.
      </p>
      {navigator.geolocation && (
        <button type="button" onClick={handleClick}>
          Use my location
        </button>
      )}
      {error && <p className="geolocation-prompt__fallback">{FALLBACK_MESSAGE}</p>}
    </div>
  )
}

export default GeolocationPrompt
