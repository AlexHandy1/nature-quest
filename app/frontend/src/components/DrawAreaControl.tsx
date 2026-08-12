import { useEffect, useState } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet-draw'
import 'leaflet-draw/dist/leaflet.draw.css'
import GeolocationPrompt from './GeolocationPrompt'
import DrawConfirmBar from './DrawConfirmBar'
import { pointsToWkt } from '../lib/polygon'

type DrawAreaControlProps = {
  onConfirm: (polygon: string, center: [number, number]) => void
}

function centroid(vertices: [number, number][]): [number, number] {
  const lat = vertices.reduce((sum, [v]) => sum + v, 0) / vertices.length
  const lon = vertices.reduce((sum, [, v]) => sum + v, 0) / vertices.length
  return [lat, lon]
}

function DrawAreaControl({ onConfirm }: DrawAreaControlProps) {
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
      edit: { featureGroup: drawnItems, remove: false },
    })
    map.addControl(drawControl)

    function captureVertices(layer: L.Polygon) {
      const latlngs = (layer.getLatLngs()[0] as L.LatLng[]).map(
        (ll): [number, number] => [ll.lat, ll.lng]
      )
      setVertices(latlngs)
    }

    function onCreated(e: L.LeafletEvent) {
      const created = e as L.DrawEvents.Created
      drawnItems.clearLayers()
      drawnItems.addLayer(created.layer)
      captureVertices(created.layer as L.Polygon)
    }
    function onEdited() {
      const layers = drawnItems.getLayers()
      if (layers.length) captureVertices(layers[0] as L.Polygon)
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
  }, [map])

  function handleUseMyLocation(lat: number, lon: number) {
    map.setView([lat, lon], map.getZoom())
  }

  function handleConfirm() {
    onConfirm(pointsToWkt(vertices), centroid(vertices))
  }

  return (
    <div className="draw-area-control">
      <GeolocationPrompt onLocated={handleUseMyLocation} />
      <DrawConfirmBar vertices={vertices} onConfirm={handleConfirm} />
    </div>
  )
}

export default DrawAreaControl
