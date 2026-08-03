import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import InterestForm from './InterestForm'

test('renders the form label, input, and submit button', () => {
  render(<InterestForm />)

  expect(
    screen.getByLabelText('What would you want to see on a walk?')
  ).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Count me in' })).toBeInTheDocument()
})

test('submitting calls POST /api/interest with the entered query', async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201 })
  vi.stubGlobal('fetch', fetchMock)

  render(<InterestForm />)
  await user.type(
    screen.getByLabelText('What would you want to see on a walk?'),
    'something rare'
  )
  await user.click(screen.getByRole('button', { name: 'Count me in' }))

  expect(fetchMock).toHaveBeenCalledWith('/api/interest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: 'something rare' }),
  })
})

test('shows a success message after a successful submission', async () => {
  const user = userEvent.setup()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 201 }))

  render(<InterestForm />)
  await user.type(
    screen.getByLabelText('What would you want to see on a walk?'),
    'something rare'
  )
  await user.click(screen.getByRole('button', { name: 'Count me in' }))

  expect(await screen.findByText(/you're on the list/i)).toBeInTheDocument()
})

test('shows an error message when the submission fails', async () => {
  const user = userEvent.setup()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422 }))

  render(<InterestForm />)
  await user.type(
    screen.getByLabelText('What would you want to see on a walk?'),
    'something rare'
  )
  await user.click(screen.getByRole('button', { name: 'Count me in' }))

  expect(await screen.findByText(/something went wrong/i)).toBeInTheDocument()
})
