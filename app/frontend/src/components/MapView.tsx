import { useEffect, useState } from 'react'
import { MapContainer, Marker, Polygon, Polyline, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import QueryPanel, { type Outcome, type Result } from './QueryPanel'
import ResultsPanel from './ResultsPanel'
import AreaControl from './AreaControl'
import DrawAreaControl from './DrawAreaControl'
import { getDistinctId, hasConsent, trackEvent } from '../lib/posthog'
import { wktToPoints } from '../lib/polygon'

// Centroid of the fixed Retiro Park polygon (services/gbif_client.py's
// GBIF_POLYGON) — kept in sync manually since the frontend doesn't share
// the backend's polygon_centroid() computation.
const RETIRO_CENTER: [number, number] = [40.4137, -3.6826]
// Must match services/gbif_client.py's GBIF_POLYGON exactly.
const RETIRO_POLYGON =
  'POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,' +
  '-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))'
const DEFAULT_ZOOM = 15
// Low enough to pan out to a world view when drawing an area anywhere.
const MIN_ZOOM = 2
const MAX_ZOOM = 18

type AreaMode = 'fixed' | 'draw'

type AreaState = {
  mode: AreaMode
  polygon: string
  center: [number, number]
}

export type Species = {
  species: string
  count: number
  kingdom: string
  hotspot_lat: number
  hotspot_lon: number
  distance_m?: number
}

function MapRecenter({ center }: { center: [number, number] }) {
  const map = useMap()
  useEffect(() => {
    map.setView(center, map.getZoom())
  }, [center, map])
  return null
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
  const [drawing, setDrawing] = useState(false)
  const [geolocationOffered, setGeolocationOffered] = useState(false)

  function selectFixedArea() {
    setAreaState({ mode: 'fixed', polygon: RETIRO_POLYGON, center: RETIRO_CENTER })
    setDrawing(false)
  }

  function startDrawing() {
    setDrawing(true)
  }

  function confirmDrawnArea(polygon: string, center: [number, number]) {
    setAreaState({ mode: 'draw', polygon, center })
    setDrawing(false)
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
    <div className="app-shell">
      <header className="nav-bar">
        <div className="nav-bar__brand">
          <h1 className="nav-bar__wordmark">🌿 Nature Quest</h1>
          <span className="nav-bar__tagline">Create a walk from real species in an area you draw</span>
        </div>
        <AreaControl
          mode={areaState.mode}
          onSelectFixed={selectFixedArea}
          onStartDraw={startDrawing}
        />
        <QueryPanel
          query={query}
          onQueryChange={setQuery}
          loading={loading}
          result={result}
          onSubmit={handleSubmit}
        />
      </header>
      <div className="map-view">
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
          {/* Leaflet pathOptions take JS color strings, not CSS custom properties —
              these must be kept in sync by hand with --accent/--text in index.css. */}
          <Polygon
            positions={wktToPoints(areaState.polygon)}
            pathOptions={{ color: '#3f6b4a', weight: 2, fillOpacity: 0.08 }}
          />
          {routeCoords.length > 1 && (
            <Polyline positions={routeCoords} pathOptions={{ color: '#5a4f3d', dashArray: '8,7' }} />
          )}
          {species.map((s, index) => (
            <Marker
              key={s.species}
              position={[s.hotspot_lat, s.hotspot_lon]}
              icon={numberedIcon(index + 1)}
            />
          ))}
          {drawing && (
            <DrawAreaControl
              onConfirm={confirmDrawnArea}
              geolocationOffered={geolocationOffered}
              onGeolocationOffered={() => setGeolocationOffered(true)}
            />
          )}
          <MapRecenter center={areaState.center} />
        </MapContainer>
        <ResultsPanel species={species} />
      </div>
    </div>
  )
}

export default MapView
