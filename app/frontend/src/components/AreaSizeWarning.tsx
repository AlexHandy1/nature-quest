import { validatePolygonPoints } from '../lib/polygon'

type AreaSizeWarningProps = {
  vertices: [number, number][]
}

function AreaSizeWarning({ vertices }: AreaSizeWarningProps) {
  if (vertices.length === 0) return null
  const validation = validatePolygonPoints(vertices)
  if (validation.valid || validation.reason !== 'area_too_large') return null

  return (
    <p className="area-size-warning">
      That area is too large — please delete it and draw a smaller shape (max 50 km²).
    </p>
  )
}

export default AreaSizeWarning
