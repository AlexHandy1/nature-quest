import { render, screen } from '@testing-library/react'
import { test, expect } from 'vitest'
import AreaSizeWarning from './AreaSizeWarning'

test('shows nothing for an empty shape', () => {
  render(<AreaSizeWarning vertices={[]} />)

  expect(screen.queryByText(/too large/i)).not.toBeInTheDocument()
})

test('shows nothing for a valid small polygon', () => {
  render(
    <AreaSizeWarning
      vertices={[
        [40.4199, -3.68876],
        [40.40777, -3.689],
        [40.4076, -3.67912],
      ]}
    />
  )

  expect(screen.queryByText(/too large/i)).not.toBeInTheDocument()
})

test('shows an inline message when the shape exceeds the 25 km^2 cap', () => {
  render(
    <AreaSizeWarning
      vertices={[
        [40.0, -4.0],
        [40.0, -3.5],
        [40.5, -3.5],
        [40.5, -4.0],
      ]}
    />
  )

  expect(screen.getByText(/too large/i)).toBeInTheDocument()
})
