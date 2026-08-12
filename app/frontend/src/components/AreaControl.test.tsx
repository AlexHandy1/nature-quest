import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect } from 'vitest'
import AreaControl from './AreaControl'

test('in fixed mode, shows the current area and an option to draw a custom one', () => {
  render(<AreaControl mode="fixed" onSelectFixed={vi.fn()} onStartDraw={vi.fn()} />)

  expect(screen.getByText(/retiro park/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /draw your own area/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /explore retiro park/i })).not.toBeInTheDocument()
})

test('in draw mode, shows an option to redraw and to switch back to Retiro', () => {
  render(<AreaControl mode="draw" onSelectFixed={vi.fn()} onStartDraw={vi.fn()} />)

  expect(screen.getByRole('button', { name: /redraw area/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /explore retiro park/i })).toBeInTheDocument()
})

test('calls onStartDraw when "Draw your own area" is clicked', async () => {
  const onStartDraw = vi.fn()
  const user = userEvent.setup()
  render(<AreaControl mode="fixed" onSelectFixed={vi.fn()} onStartDraw={onStartDraw} />)

  await user.click(screen.getByRole('button', { name: /draw your own area/i }))

  expect(onStartDraw).toHaveBeenCalledOnce()
})

test('calls onStartDraw when "Redraw area" is clicked', async () => {
  const onStartDraw = vi.fn()
  const user = userEvent.setup()
  render(<AreaControl mode="draw" onSelectFixed={vi.fn()} onStartDraw={onStartDraw} />)

  await user.click(screen.getByRole('button', { name: /redraw area/i }))

  expect(onStartDraw).toHaveBeenCalledOnce()
})

test('calls onSelectFixed when "Explore Retiro Park" is clicked', async () => {
  const onSelectFixed = vi.fn()
  const user = userEvent.setup()
  render(<AreaControl mode="draw" onSelectFixed={onSelectFixed} onStartDraw={vi.fn()} />)

  await user.click(screen.getByRole('button', { name: /explore retiro park/i }))

  expect(onSelectFixed).toHaveBeenCalledOnce()
})
