import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { test, expect, vi, beforeEach } from 'vitest'
import NarrationControl from './NarrationControl'
import { hasConsent } from '../lib/posthog'

vi.mock('../lib/posthog', () => ({
  hasConsent: vi.fn(() => false),
}))

const SPECIES = [
  { species: 'Turdus merula', count: 42, kingdom: 'Animalia', hotspot_lat: 40.41, hotspot_lon: -3.68, common_name: 'Blackbird', extract: 'A common thrush.' },
  { species: 'Pica pica', count: 30, kingdom: 'Animalia', hotspot_lat: 40.42, hotspot_lon: -3.69, common_name: 'Magpie', extract: 'A clever corvid.' },
]

function jsonResponse(status: number, body: unknown) {
  return { ok: status < 400, status, json: () => Promise.resolve(body) }
}

beforeEach(() => {
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:mock-audio-url') })
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
  HTMLMediaElement.prototype.pause = vi.fn()
})

test('shows a "Create narrative" button', () => {
  render(<NarrationControl species={SPECIES} distinctId="anon-1" />)

  expect(screen.getByRole('button', { name: /create narrative/i })).toBeInTheDocument()
})

test('clicking the button posts the species to /api/narrate', async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse(200, { narrative: 'A walk through the park.', audio: btoa('fake-mp3-bytes') })
  )
  vi.stubGlobal('fetch', fetchMock)
  render(<NarrationControl species={SPECIES} distinctId="anon-1" />)

  await user.click(screen.getByRole('button', { name: /create narrative/i }))

  const [url, options] = fetchMock.mock.calls[0]
  expect(url).toBe('/api/narrate')
  const sentBody = JSON.parse(options.body)
  expect(sentBody.distinctId).toBe('anon-1')
  expect(sentBody.species).toEqual([
    { common_name: 'Blackbird', species: 'Turdus merula', hotspot_lat: 40.41, hotspot_lon: -3.68, extract: 'A common thrush.' },
    { common_name: 'Magpie', species: 'Pica pica', hotspot_lat: 40.42, hotspot_lon: -3.69, extract: 'A clever corvid.' },
  ])
})

test('shows a disabled loading state while the request is in flight', async () => {
  const user = userEvent.setup()
  let resolveFetch: (value: unknown) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise((resolve) => { resolveFetch = resolve })))
  render(<NarrationControl species={SPECIES} distinctId="anon-1" />)

  await user.click(screen.getByRole('button', { name: /create narrative/i }))

  const button = screen.getByRole('button', { name: /generating/i })
  expect(button).toBeDisabled()
  resolveFetch!(jsonResponse(200, { narrative: 'A walk.', audio: btoa('bytes') }))
})

test('on success, the button becomes a play control and the transcript stays hidden until toggled', async () => {
  const user = userEvent.setup()
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      jsonResponse(200, { narrative: 'A walk through the park.', audio: btoa('fake-mp3-bytes') })
    )
  )
  render(<NarrationControl species={SPECIES} distinctId="anon-1" />)

  await user.click(screen.getByRole('button', { name: /create narrative/i }))

  const playButton = await screen.findByRole('button', { name: /^play$/i })
  expect(screen.queryByText('A walk through the park.')).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /show transcript/i }))
  expect(screen.getByText('A walk through the park.')).toBeInTheDocument()

  await user.click(playButton)
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
})

test('on failure, shows an error message and reverts to a clickable "Create narrative" button', async () => {
  const user = userEvent.setup()
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(jsonResponse(502, { status: 'tts_unavailable', message: 'Try again shortly.' }))
  )
  render(<NarrationControl species={SPECIES} distinctId="anon-1" />)

  await user.click(screen.getByRole('button', { name: /create narrative/i }))

  await waitFor(() => expect(screen.getByText('Try again shortly.')).toBeInTheDocument())
  expect(screen.getByRole('button', { name: /create narrative/i })).toBeEnabled()
})

test('sends consent from hasConsent()', async () => {
  vi.mocked(hasConsent).mockReturnValue(true)
  const user = userEvent.setup()
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { narrative: 'A walk.', audio: btoa('bytes') }))
  vi.stubGlobal('fetch', fetchMock)
  render(<NarrationControl species={SPECIES} distinctId="anon-1" />)

  await user.click(screen.getByRole('button', { name: /create narrative/i }))

  const [, options] = fetchMock.mock.calls[0]
  expect(JSON.parse(options.body).consent).toBe(true)
})
