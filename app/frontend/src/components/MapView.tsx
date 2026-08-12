import { useState } from 'react'
import { MapContainer, Marker, Polyline, TileLayer } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import QueryPanel, { type Outcome, type Result } from './QueryPanel'
import ResultsPanel from './ResultsPanel'
import AreaChoicePopup from './AreaChoicePopup'
import { getDistinctId, hasConsent, trackEvent } from '../lib/posthog'

// Centroid of the fixed Retiro Park polygon (services/gbif_client.py's
// GBIF_POLYGON) — kept in sync manually since the frontend doesn't share
// the backend's polygon_centroid() computation.
const RETIRO_CENTER: [number, number] = [40.4137, -3.6826]
// Must match services/gbif_client.py's GBIF_POLYGON exactly.
const RETIRO_POLYGON =
  'POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,' +
  '-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))'
const DEFAULT_ZOOM = 15
const MIN_ZOOM = 14
const MAX_ZOOM = 18

type AreaMode = 'fixed' | 'draw'

type AreaState = {
  mode: AreaMode
  polygon: string
  center: [number, number]
}

type FlowStage = 'choice' | 'drawing' | 'ready'

export type Species = {
  species: string
  count: number
  kingdom: string
  hotspot_lat: number
  hotspot_lon: number
  distance_m?: number
}

function numberedIcon(num: number) {
  return L.divIcon({
    html: `<div class="map-marker-number">${num}</div>`,
    className: '',
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  })
}

function MapView() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<Result | null>(null)
  const [species, setSpecies] = useState<Species[]>([])
  const [areaState, setAreaState] = useState<AreaState>({
    mode: 'fixed',
    polygon: RETIRO_POLYGON,
    center: RETIRO_CENTER,
  })
  const [flowStage, setFlowStage] = useState<FlowStage>('choice')

  function selectFixedArea() {
    setAreaState({ mode: 'fixed', polygon: RETIRO_POLYGON, center: RETIRO_CENTER })
    setFlowStage('ready')
  }

  function startDrawing() {
    setFlowStage('drawing')
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    trackEvent('query_submitted')
    const response = await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        distinctId: getDistinctId(),
        consent: hasConsent(),
        polygon: areaState.polygon,
      }),
    })
    const body = await response.json()
    const outcome: Outcome = body.status ?? body.error
    setLoading(false)
    trackEvent('query_outcome', { status: outcome })
    setResult({ outcome, message: body.message })
    setSpecies(outcome === 'resolved' ? body.species : [])
  }

  const routeCoords = species.map((s): [number, number] => [s.hotspot_lat, s.hotspot_lon])

  return (
    <div className="map-view">
      <h1 className="map-view__title">Nature Quest</h1>
      <MapContainer
        center={areaState.center}
        zoom={DEFAULT_ZOOM}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        className="map-view__map"
      >
        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {routeCoords.length > 1 && (
          <Polyline positions={routeCoords} pathOptions={{ color: '#333', dashArray: '8,7' }} />
        )}
        {species.map((s, index) => (
          <Marker
            key={s.species}
            position={[s.hotspot_lat, s.hotspot_lon]}
            icon={numberedIcon(index + 1)}
          />
        ))}
      </MapContainer>
      {flowStage === 'choice' && (
        <AreaChoicePopup onSelectFixed={selectFixedArea} onSelectDraw={startDrawing} />
      )}
      {flowStage === 'ready' && (
        <QueryPanel
          query={query}
          onQueryChange={setQuery}
          loading={loading}
          result={result}
          onSubmit={handleSubmit}
          docked={result?.outcome === 'resolved'}
        />
      )}
      <ResultsPanel species={species} />
    </div>
  )
}

export default MapView
