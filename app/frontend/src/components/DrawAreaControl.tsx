import { useEffect, useState } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet-draw'
import 'leaflet-draw/dist/leaflet.draw.css'
import GeolocationPrompt from './GeolocationPrompt'
import AreaSizeWarning from './AreaSizeWarning'
import { pointsToWkt, validatePolygonPoints } from '../lib/polygon'

type DrawAreaControlProps = {
  onConfirm: (polygon: string, center: [number, number]) => void
  geolocationOffered: boolean
  onGeolocationOffered: () => void
}

function centroid(vertices: [number, number][]): [number, number] {
  const lat = vertices.reduce((sum, [v]) => sum + v, 0) / vertices.length
  const lon = vertices.reduce((sum, [, v]) => sum + v, 0) / vertices.length
  return [lat, lon]
}

function layerToPoints(layer: L.Polygon): [number, number][] {
  return (layer.getLatLngs()[0] as L.LatLng[]).map((ll): [number, number] => [ll.lat, ll.lng])
}

function DrawAreaControl({
  onConfirm,
  geolocationOffered,
  onGeolocationOffered,
}: DrawAreaControlProps) {
  const map = useMap()
  const [vertices, setVertices] = useState<[number, number][]>([])

  useEffect(() => {
    const drawnItems = new L.FeatureGroup()
    map.addLayer(drawnItems)
    const drawControl = new L.Control.Draw({
      draw: {
        polygon: {},
        marker: false,
        circle: false,
        circlemarker: false,
        rectangle: false,
        polyline: false,
      },
      edit: { featureGroup: drawnItems, remove: true },
    })
    map.addControl(drawControl)

    function commitIfValid(points: [number, number][]) {
      setVertices(points)
      if (validatePolygonPoints(points).valid) {
        onConfirm(pointsToWkt(points), centroid(points))
      }
    }

    function onCreated(e: L.LeafletEvent) {
      const created = e as L.DrawEvents.Created
      drawnItems.clearLayers()
      drawnItems.addLayer(created.layer)
      commitIfValid(layerToPoints(created.layer as L.Polygon))
    }
    function onEdited() {
      const layers = drawnItems.getLayers()
      if (!layers.length) return
      commitIfValid(layerToPoints(layers[0] as L.Polygon))
    }
    function onDeleted() {
      setVertices([])
    }

    map.on(L.Draw.Event.CREATED, onCreated)
    map.on(L.Draw.Event.EDITED, onEdited)
    map.on(L.Draw.Event.DELETED, onDeleted)

    return () => {
      map.off(L.Draw.Event.CREATED, onCreated)
      map.off(L.Draw.Event.EDITED, onEdited)
      map.off(L.Draw.Event.DELETED, onDeleted)
      map.removeControl(drawControl)
      map.removeLayer(drawnItems)
    }
  }, [map, onConfirm])

  function handleUseMyLocation(lat: number, lon: number) {
    map.setView([lat, lon], map.getZoom())
  }

  return (
    <div className="draw-area-control">
      {!geolocationOffered && (
        <GeolocationPrompt onLocated={handleUseMyLocation} onOffered={onGeolocationOffered} />
      )}
      <AreaSizeWarning vertices={vertices} />
    </div>
  )
}

export default DrawAreaControl
