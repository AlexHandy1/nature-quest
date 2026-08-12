import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect } from 'vitest'
import AreaChoicePopup from './AreaChoicePopup'

test('renders both area choice options', () => {
  render(<AreaChoicePopup onSelectFixed={vi.fn()} onSelectDraw={vi.fn()} />)

  expect(screen.getByRole('button', { name: /explore retiro park/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /draw your own area/i })).toBeInTheDocument()
})

test('calls onSelectFixed when "Explore Retiro Park" is clicked', async () => {
  const onSelectFixed = vi.fn()
  const user = userEvent.setup()
  render(<AreaChoicePopup onSelectFixed={onSelectFixed} onSelectDraw={vi.fn()} />)

  await user.click(screen.getByRole('button', { name: /explore retiro park/i }))

  expect(onSelectFixed).toHaveBeenCalledOnce()
})

test('calls onSelectDraw when "Draw your own area" is clicked', async () => {
  const onSelectDraw = vi.fn()
  const user = userEvent.setup()
  render(<AreaChoicePopup onSelectFixed={vi.fn()} onSelectDraw={onSelectDraw} />)

  await user.click(screen.getByRole('button', { name: /draw your own area/i }))

  expect(onSelectDraw).toHaveBeenCalledOnce()
})
