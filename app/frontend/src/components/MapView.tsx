import { useEffect, useState } from 'react'
import { MapContainer, Marker, Polygon, Polyline, Rectangle, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import QueryPanel, { type Outcome, type Result } from './QueryPanel'
import ResultsPanel from './ResultsPanel'
import AreaControl from './AreaControl'
import DrawAreaControl from './DrawAreaControl'
import { getDistinctId, hasConsent, trackEvent } from '../lib/posthog'
import { wktToPoints } from '../lib/polygon'

// Centroid of the fixed Retiro Park polygon below — kept in sync manually
// since the frontend doesn't share the backend's polygon_centroid()
// computation (services/species_index.py).
const RETIRO_CENTER: [number, number] = [40.4137, -3.6826]
// This app's canonical fixed Retiro Park polygon, used as the default
// search area and reused verbatim in the backend's own tests.
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
  cell: string
  cell_lat_err: number
  cell_lon_err: number
  distance_m?: number
  common_name?: string | null
  image_url?: string | null
  extract?: string | null
}

// Several species often share the same highest-count cell — the aggregated
// fact table's actual resolution, not a precise sighting point. Species
// sharing a cell are spread to distinct points within that cell's footprint
// (drawn separately, once per distinct cell) rather than stacked on its
// centroid, so multiple pins stay visible without implying more precision
// than the footprint itself already shows.
function pinPosition(s: Species, siblingsInSameCell: Species[]): [number, number] {
  const index = siblingsInSameCell.indexOf(s)
  const count = siblingsInSameCell.length
  const angle = (2 * Math.PI * index) / count
  const spread = count > 1 ? 0.55 : 0 // fraction of the cell's half-width/half-height
  return [
    s.hotspot_lat + spread * s.cell_lat_err * Math.sin(angle),
    s.hotspot_lon + spread * s.cell_lon_err * Math.cos(angle),
  ]
}

function groupByCell(species: Species[]): Record<string, Species[]> {
  const groups: Record<string, Species[]> = {}
  for (const s of species) {
    ;(groups[s.cell] ??= []).push(s)
  }
  return groups
}

function distinctCellFootprints(species: Species[]): Species[] {
  const seen = new Map<string, Species>()
  for (const s of species) {
    if (!seen.has(s.cell)) seen.set(s.cell, s)
  }
  return Array.from(seen.values())
}

function MapRecenter({ center }: { center: [number, number] }) {
  const map = useMap()
  useEffect(() => {
    map.setView(center, map.getZoom())
  }, [center, map])
  return null
}

function numberedIcon(num: number, active: boolean) {
  return L.divIcon({
    html: `<div class="map-marker-number${active ? ' map-marker-number--active' : ''}">${num}</div>`,
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
  const [expandedSpecies, setExpandedSpecies] = useState<string | null>(null)
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

  function toggleExpandedSpecies(species: string) {
    setExpandedSpecies((current) => (current === species ? null : species))
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
    setExpandedSpecies(null)
  }

  const speciesByCell = groupByCell(species)
  const pinPositions = species.map((s) => pinPosition(s, speciesByCell[s.cell]))
  const cellFootprints = distinctCellFootprints(species)

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
          {pinPositions.length > 1 && (
            <Polyline positions={pinPositions} pathOptions={{ color: '#5a4f3d', dashArray: '8,7' }} />
          )}
          {cellFootprints.map((s) => (
            <Rectangle
              key={s.cell}
              bounds={[
                [s.hotspot_lat - s.cell_lat_err, s.hotspot_lon - s.cell_lon_err],
                [s.hotspot_lat + s.cell_lat_err, s.hotspot_lon + s.cell_lon_err],
              ]}
              pathOptions={{ color: '#3f6b4a', weight: 1.5, fillOpacity: 0.12, dashArray: '4,3' }}
            />
          ))}
          {species.map((s, index) => (
            <Marker
              key={s.species}
              position={pinPositions[index]}
              icon={numberedIcon(index + 1, s.species === expandedSpecies)}
              eventHandlers={{ click: () => toggleExpandedSpecies(s.species) }}
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
        <ResultsPanel
          species={species}
          expandedSpecies={expandedSpecies}
          onToggleSpecies={toggleExpandedSpecies}
          distinctId={getDistinctId()}
        />
      </div>
    </div>
  )
}

export default MapView
