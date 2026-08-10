import { render, screen } from '@testing-library/react'
import { test, expect } from 'vitest'
import ResultsPanel from './ResultsPanel'

const SPECIES = [
  { species: 'Turdus merula', count: 42, kingdom: 'Animalia', hotspot_lat: 40.41, hotspot_lon: -3.68 },
  { species: 'Pica pica', count: 30, kingdom: 'Animalia', hotspot_lat: 40.42, hotspot_lon: -3.69 },
]

test('lists each species scientific name and observation count in route order', () => {
  render(<ResultsPanel species={SPECIES} />)

  const items = screen.getAllByRole('listitem')
  expect(items).toHaveLength(2)
  expect(items[0]).toHaveTextContent('Turdus merula')
  expect(items[0]).toHaveTextContent('42')
  expect(items[1]).toHaveTextContent('Pica pica')
  expect(items[1]).toHaveTextContent('30')
})

test('renders nothing when there are no species', () => {
  const { container } = render(<ResultsPanel species={[]} />)

  expect(container).toBeEmptyDOMElement()
})
