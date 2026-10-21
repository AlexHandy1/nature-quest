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

// Must match models/query.py's MIN_POLYGON_VERTICES.
const MIN_POLYGON_VERTICES = 3

export type PolygonValidation = { valid: true } | { valid: false; reason: 'too_few_vertices' }

export function validatePolygonPoints(points: [number, number][]): PolygonValidation {
  if (points.length < MIN_POLYGON_VERTICES) {
    return { valid: false, reason: 'too_few_vertices' }
  }
  return { valid: true }
}
