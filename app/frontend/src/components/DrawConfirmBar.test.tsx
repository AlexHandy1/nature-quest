import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect } from 'vitest'
import DrawConfirmBar from './DrawConfirmBar'

test('disables confirm when no polygon has been drawn', () => {
  render(<DrawConfirmBar vertices={[]} onConfirm={vi.fn()} />)

  expect(screen.getByRole('button', { name: /confirm area/i })).toBeDisabled()
})

test('disables confirm with fewer than 3 vertices', () => {
  render(
    <DrawConfirmBar
      vertices={[
        [40.42, -3.68],
        [40.41, -3.69],
      ]}
      onConfirm={vi.fn()}
    />
  )

  expect(screen.getByRole('button', { name: /confirm area/i })).toBeDisabled()
})

test('disables confirm and shows an inline message when the area exceeds 25 km^2', () => {
  render(
    <DrawConfirmBar
      vertices={[
        [40.0, -4.0],
        [40.0, -3.5],
        [40.5, -3.5],
        [40.5, -4.0],
      ]}
      onConfirm={vi.fn()}
    />
  )

  expect(screen.getByRole('button', { name: /confirm area/i })).toBeDisabled()
  expect(screen.getByText(/too large/i)).toBeInTheDocument()
})

test('enables confirm for a valid polygon and calls onConfirm when clicked', async () => {
  const onConfirm = vi.fn()
  const user = userEvent.setup()
  render(
    <DrawConfirmBar
      vertices={[
        [40.4199, -3.68876],
        [40.40777, -3.689],
        [40.4076, -3.67912],
      ]}
      onConfirm={onConfirm}
    />
  )

  const button = screen.getByRole('button', { name: /confirm area/i })
  expect(button).not.toBeDisabled()
  await user.click(button)

  expect(onConfirm).toHaveBeenCalledOnce()
})
