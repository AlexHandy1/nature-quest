export function wktToPoints(wkt: string): [number, number][] {
  const ring = wkt.replace(/^POLYGON\(\(/, '').replace(/\)\)$/, '')
  const pairs = ring.split(',').map((pair) => {
    const [lon, lat] = pair.trim().split(/\s+/).map(Number)
    return [lat, lon] as [number, number]
  })
  const first = pairs[0]
  const last = pairs[pairs.length - 1]
  if (first[0] === last[0] && first[1] === last[1]) {
    pairs.pop()
  }
  return pairs
}

export function pointsToWkt(points: [number, number][]): string {
  const ring = points.map(([lat, lon]) => [lon, lat] as [number, number])
  const first = ring[0]
  const last = ring[ring.length - 1]
  if (first[0] !== last[0] || first[1] !== last[1]) {
    ring.push(first)
  }
  const coords = ring.map(([lon, lat]) => `${lon} ${lat}`).join(',')
  return `POLYGON((${coords}))`
}

// Must match models/query.py's MIN_POLYGON_VERTICES / MAX_POLYGON_AREA_KM2.
const MIN_POLYGON_VERTICES = 3
const MAX_POLYGON_AREA_KM2 = 25.0
const KM_PER_DEGREE_LAT = 111.0

export type PolygonValidation =
  | { valid: true }
  | { valid: false; reason: 'too_few_vertices' | 'area_too_large' }

function boundingAreaKm2(points: [number, number][]): number {
  const lats = points.map(([lat]) => lat)
  const lons = points.map(([, lon]) => lon)
  const meanLat = lats.reduce((sum, lat) => sum + lat, 0) / lats.length
  const heightKm = (Math.max(...lats) - Math.min(...lats)) * KM_PER_DEGREE_LAT
  const widthKm =
    (Math.max(...lons) - Math.min(...lons)) *
    KM_PER_DEGREE_LAT *
    Math.cos((meanLat * Math.PI) / 180)
  return heightKm * widthKm
}

export function validatePolygonPoints(points: [number, number][]): PolygonValidation {
  if (points.length < MIN_POLYGON_VERTICES) {
    return { valid: false, reason: 'too_few_vertices' }
  }
  if (boundingAreaKm2(points) > MAX_POLYGON_AREA_KM2) {
    return { valid: false, reason: 'area_too_large' }
  }
  return { valid: true }
}
