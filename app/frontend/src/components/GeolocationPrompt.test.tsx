import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect, beforeEach, afterEach } from 'vitest'
import GeolocationPrompt from './GeolocationPrompt'

const originalGeolocation = navigator.geolocation

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  Object.defineProperty(navigator, 'geolocation', {
    value: originalGeolocation,
    configurable: true,
  })
})

function stubGeolocation(impl: (
  success: PositionCallback,
  error?: PositionErrorCallback
) => void) {
  Object.defineProperty(navigator, 'geolocation', {
    value: { getCurrentPosition: vi.fn(impl) },
    configurable: true,
  })
}

test('shows a location button and a skip control', () => {
  stubGeolocation(() => {})
  render(<GeolocationPrompt onLocated={vi.fn()} onOffered={vi.fn()} />)

  expect(screen.getByRole('button', { name: /use my location/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /skip/i })).toBeInTheDocument()
})

test('calls onLocated and onOffered on successful geolocation', async () => {
  stubGeolocation((success) => {
    success({ coords: { latitude: 40.9, longitude: -3.87 } } as GeolocationPosition)
  })
  const onLocated = vi.fn()
  const onOffered = vi.fn()
  const user = userEvent.setup()
  render(<GeolocationPrompt onLocated={onLocated} onOffered={onOffered} />)

  await user.click(screen.getByRole('button', { name: /use my location/i }))

  expect(onLocated).toHaveBeenCalledWith(40.9, -3.87)
  expect(onOffered).toHaveBeenCalledOnce()
})

test('calls onOffered (not onLocated) and shows a fallback message when geolocation errors', async () => {
  stubGeolocation((_success, error) => {
    error?.({ code: 1, message: 'denied' } as GeolocationPositionError)
  })
  const onLocated = vi.fn()
  const onOffered = vi.fn()
  const user = userEvent.setup()
  render(<GeolocationPrompt onLocated={onLocated} onOffered={onOffered} />)

  await user.click(screen.getByRole('button', { name: /use my location/i }))

  expect(await screen.findByText(/couldn't get your location/i)).toBeInTheDocument()
  expect(onLocated).not.toHaveBeenCalled()
  expect(onOffered).toHaveBeenCalledOnce()
})

test('calls onOffered without querying location when skipped', async () => {
  stubGeolocation(() => {})
  const onLocated = vi.fn()
  const onOffered = vi.fn()
  const user = userEvent.setup()
  render(<GeolocationPrompt onLocated={onLocated} onOffered={onOffered} />)

  await user.click(screen.getByRole('button', { name: /skip/i }))

  expect(onOffered).toHaveBeenCalledOnce()
  expect(onLocated).not.toHaveBeenCalled()
})

test('shows only the fallback message when navigator.geolocation is unavailable', () => {
  Object.defineProperty(navigator, 'geolocation', { value: undefined, configurable: true })
  render(<GeolocationPrompt onLocated={vi.fn()} onOffered={vi.fn()} />)

  expect(screen.getByText(/couldn't get your location/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /use my location/i })).not.toBeInTheDocument()
})
