import { useRef, useState } from 'react'
import type { Species } from './MapView'
import { hasConsent } from '../lib/posthog'

type Status = 'idle' | 'loading' | 'ready' | 'error'

type NarrationControlProps = {
  species: Species[]
  distinctId: string
}

function base64ToBlobUrl(base64: string): string {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))
  const blob = new Blob([bytes], { type: 'audio/mpeg' })
  return URL.createObjectURL(blob)
}

function NarrationControl({ species, distinctId }: NarrationControlProps) {
  const [status, setStatus] = useState<Status>('idle')
  const [narrative, setNarrative] = useState<string | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [showTranscript, setShowTranscript] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement>(null)

  async function handleGenerate() {
    setStatus('loading')
    setErrorMessage(null)
    const response = await fetch('/api/narrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        species: species.map((s) => ({
          common_name: s.common_name ?? s.species,
          species: s.species,
          hotspot_lat: s.hotspot_lat,
          hotspot_lon: s.hotspot_lon,
          extract: s.extract ?? null,
        })),
        distinctId,
        consent: hasConsent(),
      }),
    })
    const body = await response.json()
    if (!response.ok) {
      setStatus('error')
      setErrorMessage(body.message ?? 'Something went wrong.')
      return
    }
    setAudioUrl(base64ToBlobUrl(body.audio))
    setNarrative(body.narrative)
    setStatus('ready')
  }

  function togglePlayback() {
    const audio = audioRef.current
    if (!audio) return
    if (isPlaying) {
      audio.pause()
    } else {
      audio.play()
    }
  }

  if (species.length === 0) return null

  return (
    <>
      {status !== 'ready' && (
        <button
          type="button"
          className="narration-control__button"
          disabled={status === 'loading'}
          onClick={handleGenerate}
        >
          {status === 'loading' ? 'Generating…' : 'Create narrative'}
        </button>
      )}
      {status === 'ready' && (
        <button type="button" className="narration-control__button" onClick={togglePlayback}>
          {isPlaying ? 'Pause' : 'Play'}
        </button>
      )}
      {audioUrl && (
        <audio
          ref={audioRef}
          src={audioUrl}
          data-testid="narration-audio"
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onEnded={() => setIsPlaying(false)}
          style={{ display: 'none' }}
        />
      )}
      {status === 'error' && errorMessage && <p className="narration-control__error">{errorMessage}</p>}
      {narrative && (
        <div className="narration-control__transcript-row">
          <button
            type="button"
            className="narration-control__transcript-toggle"
            onClick={() => setShowTranscript((current) => !current)}
          >
            {showTranscript ? 'Hide transcript' : 'Show transcript'}
          </button>
          {showTranscript && <p className="narration-control__transcript">{narrative}</p>}
        </div>
      )}
    </>
  )
}

export default NarrationControl
