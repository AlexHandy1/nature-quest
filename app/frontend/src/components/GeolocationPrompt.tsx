import { useState } from 'react'

type GeolocationPromptProps = {
  onLocated: (lat: number, lon: number) => void
  onOffered: () => void
}

const FALLBACK_MESSAGE = "Couldn't get your location — pan and zoom manually."

function GeolocationPrompt({ onLocated, onOffered }: GeolocationPromptProps) {
  const [error, setError] = useState(!navigator.geolocation)

  function handleClick() {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        onLocated(position.coords.latitude, position.coords.longitude)
        onOffered()
      },
      () => {
        setError(true)
        onOffered()
      }
    )
  }

  return (
    <div className="geolocation-prompt">
      {navigator.geolocation && (
        <>
          <button
            type="button"
            className="geolocation-prompt__button"
            title="Center the map on your location"
            onClick={handleClick}
          >
            📍 Use my location
          </button>
          <button type="button" className="geolocation-prompt__skip" onClick={onOffered}>
            Skip
          </button>
        </>
      )}
      {error && <p className="geolocation-prompt__fallback">{FALLBACK_MESSAGE}</p>}
    </div>
  )
}

export default GeolocationPrompt
