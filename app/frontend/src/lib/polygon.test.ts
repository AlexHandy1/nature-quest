import { test, expect } from 'vitest'
import { pointsToWkt, validatePolygonPoints, wktToPoints } from './polygon'

test('converts a WKT polygon back to [lat, lon] points, dropping the closing repeat', () => {
  const wkt =
    'POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.68876 40.4199))'

  expect(wktToPoints(wkt)).toEqual([
    [40.4199, -3.68876],
    [40.40777, -3.689],
    [40.4076, -3.67912],
  ])
})

test('converts Leaflet [lat, lon] points to a closed WKT polygon in "lon lat" order', () => {
  const points: [number, number][] = [
    [40.4199, -3.68876],
    [40.40777, -3.689],
    [40.4076, -3.67912],
  ]

  const wkt = pointsToWkt(points)

  expect(wkt).toBe(
    'POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.68876 40.4199))'
  )
})

test('rejects a polygon with fewer than 3 vertices', () => {
  const points: [number, number][] = [
    [40.4199, -3.68876],
    [40.40777, -3.689],
  ]

  expect(validatePolygonPoints(points)).toEqual({
    valid: false,
    reason: 'too_few_vertices',
  })
})

test('accepts a valid small polygon', () => {
  const points: [number, number][] = [
    [40.4199, -3.68876],
    [40.40777, -3.689],
    [40.4076, -3.67912],
  ]

  expect(validatePolygonPoints(points)).toEqual({ valid: true })
})

test('accepts a polygon well above the old GBIF-motivated cap', () => {
  // ~30km x 23km bounding box (~660 km^2) - well above the old, removed
  // 25 km^2 GBIF cap, comfortably under the new 2,000 km^2 one.
  const points: [number, number][] = [
    [40.0, -3.85],
    [40.0, -3.5],
    [40.2, -3.5],
    [40.2, -3.85],
  ]

  expect(validatePolygonPoints(points)).toEqual({ valid: true })
})

test('rejects a polygon exceeding the 2,000 km^2 area cap', () => {
  // ~111km x 85km bounding box (~9,400 km^2). This cap bounds
  // candidate_cells' own Python-side grid-sampling cost/memory, not
  // GBIF's rate limits - measured empirically to stay well under 1s even
  // at extreme (near-Arctic) latitudes.
  const points: [number, number][] = [
    [40.0, -4.0],
    [40.0, -3.0],
    [41.0, -3.0],
    [41.0, -4.0],
  ]

  expect(validatePolygonPoints(points)).toEqual({
    valid: false,
    reason: 'area_too_large',
  })
})
