import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect } from 'vitest'
import QueryPanel from './QueryPanel'

function renderPanel(overrides = {}) {
  const props = {
    query: '',
    onQueryChange: vi.fn(),
    loading: false,
    result: null,
    onSubmit: vi.fn((event) => event.preventDefault()),
    ...overrides,
  }
  render(<QueryPanel {...props} />)
  return props
}

test('renders the label, input, and submit button', () => {
  renderPanel()

  expect(
    screen.getByLabelText('What would you want to see on a walk?')
  ).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /create walk/i })).toBeInTheDocument()
})

test('calls onSubmit when the form is submitted', async () => {
  const user = userEvent.setup()
  const props = renderPanel({ query: 'birds' })

  await user.click(screen.getByRole('button', { name: /create walk/i }))

  expect(props.onSubmit).toHaveBeenCalledTimes(1)
})

test('calls onQueryChange as the user types', async () => {
  const user = userEvent.setup()
  const props = renderPanel()

  await user.type(screen.getByLabelText('What would you want to see on a walk?'), 'b')

  expect(props.onQueryChange).toHaveBeenCalledWith('b')
})

test('disables the input and button and shows a loading message while loading', () => {
  renderPanel({ loading: true })

  expect(screen.getByRole('button', { name: /searching/i })).toBeDisabled()
  expect(screen.getByLabelText('What would you want to see on a walk?')).toBeDisabled()
  expect(screen.getByText(/generating your walk/i)).toBeInTheDocument()
})

test('shows the outcome message for a non-resolved outcome', () => {
  renderPanel({
    result: { outcome: 'unresolved', message: "couldn't match that" },
  })

  expect(screen.getByText("couldn't match that")).toBeInTheDocument()
  expect(screen.queryByRole('list')).not.toBeInTheDocument()
})

test('hides the outcome message for a resolved outcome — results speak for themselves', () => {
  renderPanel({
    result: { outcome: 'resolved', message: 'Early preview message' },
  })

  expect(screen.queryByText('Early preview message')).not.toBeInTheDocument()
})

test('keeps the form enabled and resubmittable after a resolved result', () => {
  renderPanel({
    result: { outcome: 'resolved', message: 'ok' },
    loading: false,
  })

  expect(screen.getByLabelText('What would you want to see on a walk?')).not.toBeDisabled()
  expect(screen.getByRole('button', { name: /create walk/i })).not.toBeDisabled()
})

test('keeps the form enabled and resubmittable after a non-resolved outcome', () => {
  renderPanel({
    result: { outcome: 'unresolved', message: "couldn't match that" },
    loading: false,
  })

  expect(screen.getByLabelText('What would you want to see on a walk?')).not.toBeDisabled()
  expect(screen.getByRole('button', { name: /create walk/i })).not.toBeDisabled()
})
