import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { test, expect, vi, beforeEach } from 'vitest'
import ResultsPanel from './ResultsPanel'

const SPECIES = [
  { species: 'Turdus merula', count: 42, kingdom: 'Animalia', hotspot_lat: 40.41, hotspot_lon: -3.68 },
  { species: 'Pica pica', count: 30, kingdom: 'Animalia', hotspot_lat: 40.42, hotspot_lon: -3.69 },
]

const ENRICHED_SPECIES = [
  {
    species: 'Turdus merula',
    species_key: 2495414,
    common_name: 'Common Blackbird',
    image_url: 'https://upload.wikimedia.org/blackbird.jpg',
    count: 42,
    kingdom: 'Animalia',
    hotspot_lat: 40.41,
    hotspot_lon: -3.68,
  },
]

const UNTRUSTED_IMAGE_SPECIES = [
  {
    species: 'Turdus merula',
    species_key: 2495414,
    common_name: 'Common Blackbird',
    image_url: 'https://evil.example.com/blackbird.jpg',
    count: 42,
    kingdom: 'Animalia',
    hotspot_lat: 40.41,
    hotspot_lon: -3.68,
  },
]

function noop() {}

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) }
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:mock-audio-url')
})

test('lists each species scientific name and observation count in route order', () => {
  render(<ResultsPanel species={SPECIES} expandedSpecies={null} onToggleSpecies={noop} distinctId="anon-1" />)

  const items = screen.getAllByRole('listitem')
  expect(items).toHaveLength(2)
  expect(items[0]).toHaveTextContent('Turdus merula')
  expect(items[0]).toHaveTextContent('42')
  expect(items[1]).toHaveTextContent('Pica pica')
  expect(items[1]).toHaveTextContent('30')
})

test('renders nothing when there are no species', () => {
  const { container } = render(<ResultsPanel species={[]} expandedSpecies={null} onToggleSpecies={noop} distinctId="anon-1" />)

  expect(container).toBeEmptyDOMElement()
})

test('shows the common name as the primary label with scientific name secondary', () => {
  render(<ResultsPanel species={ENRICHED_SPECIES} expandedSpecies={null} onToggleSpecies={noop} distinctId="anon-1" />)

  expect(screen.getByText('Common Blackbird')).toBeInTheDocument()
  expect(screen.getByText('Turdus merula')).toBeInTheDocument()
})

test('falls back to the scientific name alone when no common name was found', () => {
  render(<ResultsPanel species={SPECIES} expandedSpecies={null} onToggleSpecies={noop} distinctId="anon-1" />)

  const items = screen.getAllByRole('listitem')
  expect(items[0]).toHaveTextContent('Turdus merula')
})

test('clicking a row calls onToggleSpecies with that species', async () => {
  const user = userEvent.setup()
  const onToggle = vi.fn()
  render(<ResultsPanel species={SPECIES} expandedSpecies={null} onToggleSpecies={onToggle} distinctId="anon-1" />)

  await user.click(screen.getByRole('button', { name: /turdus merula/i }))

  expect(onToggle).toHaveBeenCalledWith('Turdus merula')
})

test('shows image and a GBIF link only for the expanded species', () => {
  render(
    <ResultsPanel species={ENRICHED_SPECIES} expandedSpecies="Turdus merula" onToggleSpecies={noop} distinctId="anon-1" />
  )

  const image = screen.getByRole('img', { name: /common blackbird/i })
  expect(image).toHaveAttribute('src', 'https://upload.wikimedia.org/blackbird.jpg')

  const link = screen.getByRole('link', { name: /gbif/i })
  expect(link).toHaveAttribute('href', 'https://www.gbif.org/species/2495414')
})

test('does not show detail for a species that is not the expanded one', () => {
  render(<ResultsPanel species={ENRICHED_SPECIES} expandedSpecies={null} onToggleSpecies={noop} distinctId="anon-1" />)

  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /gbif/i })).not.toBeInTheDocument()
})

test('shows a no-image fallback when the expanded species has no image', () => {
  render(<ResultsPanel species={SPECIES} expandedSpecies="Turdus merula" onToggleSpecies={noop} distinctId="anon-1" />)

  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.getByText(/no image available/i)).toBeInTheDocument()
})

test('shows a no-image fallback when the image url is not from a trusted wikimedia host', () => {
  render(
    <ResultsPanel species={UNTRUSTED_IMAGE_SPECIES} expandedSpecies="Turdus merula" onToggleSpecies={noop} distinctId="anon-1" />
  )

  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.getByText(/no image available/i)).toBeInTheDocument()
})

test('narration resets to "Create narrative" when the species list changes to a new walk', async () => {
  const user = userEvent.setup()
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(jsonResponse({ narrative: 'First walk narrative.', audio: btoa('bytes') }))
  )
  const { rerender } = render(
    <ResultsPanel species={SPECIES} expandedSpecies={null} onToggleSpecies={noop} distinctId="anon-1" />
  )

  await user.click(screen.getByRole('button', { name: /create narrative/i }))
  await screen.findByRole('button', { name: /^play$/i })

  const NEW_WALK_SPECIES = [
    { species: 'Alopochen aegyptiaca', count: 12, kingdom: 'Animalia', hotspot_lat: 40.44, hotspot_lon: -3.68 },
  ]
  rerender(
    <ResultsPanel species={NEW_WALK_SPECIES} expandedSpecies={null} onToggleSpecies={noop} distinctId="anon-1" />
  )

  expect(screen.getByRole('button', { name: /create narrative/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^play$/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /show transcript/i })).not.toBeInTheDocument()
})
