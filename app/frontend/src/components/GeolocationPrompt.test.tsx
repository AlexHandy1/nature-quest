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

test('shows a "Use my location" button with copy explaining it only centers the map', () => {
  stubGeolocation(() => {})
  render(<GeolocationPrompt onLocated={vi.fn()} />)

  expect(screen.getByRole('button', { name: /use my location/i })).toBeInTheDocument()
  expect(screen.getByText(/only centers the map/i)).toBeInTheDocument()
  expect(screen.getByText(/never sent to the server or stored/i)).toBeInTheDocument()
})

test('calls onLocated with coordinates on successful geolocation', async () => {
  stubGeolocation((success) => {
    success({ coords: { latitude: 40.9, longitude: -3.87 } } as GeolocationPosition)
  })
  const onLocated = vi.fn()
  const user = userEvent.setup()
  render(<GeolocationPrompt onLocated={onLocated} />)

  await user.click(screen.getByRole('button', { name: /use my location/i }))

  expect(onLocated).toHaveBeenCalledWith(40.9, -3.87)
})

test('shows an inline fallback message and does not call onLocated when geolocation errors', async () => {
  stubGeolocation((_success, error) => {
    error?.({ code: 1, message: 'denied' } as GeolocationPositionError)
  })
  const onLocated = vi.fn()
  const user = userEvent.setup()
  render(<GeolocationPrompt onLocated={onLocated} />)

  await user.click(screen.getByRole('button', { name: /use my location/i }))

  expect(await screen.findByText(/couldn't get your location/i)).toBeInTheDocument()
  expect(onLocated).not.toHaveBeenCalled()
})

test('shows the fallback message directly when navigator.geolocation is unavailable', () => {
  Object.defineProperty(navigator, 'geolocation', { value: undefined, configurable: true })
  render(<GeolocationPrompt onLocated={vi.fn()} />)

  expect(screen.getByText(/couldn't get your location/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /use my location/i })).not.toBeInTheDocument()
})
