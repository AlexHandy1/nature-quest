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
    docked: false,
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
  expect(screen.getByRole('button', { name: /show me/i })).toBeInTheDocument()
})

test('renders the Nature Quest heading when not docked', () => {
  renderPanel({ docked: false })
  expect(screen.getByRole('heading', { name: 'Nature Quest' })).toBeInTheDocument()
})

test('renders the Nature Quest heading when docked', () => {
  renderPanel({ docked: true })
  expect(screen.getByRole('heading', { name: 'Nature Quest' })).toBeInTheDocument()
})

test('shows the explanatory intro copy when not docked', () => {
  renderPanel({ docked: false })

  expect(screen.getByText(/tell us what you'd like to see/i)).toBeInTheDocument()
})

test('hides the explanatory intro copy when docked', () => {
  renderPanel({ docked: true })

  expect(screen.queryByText(/tell us what you'd like to see/i)).not.toBeInTheDocument()
})

test('calls onSubmit when the form is submitted', async () => {
  const user = userEvent.setup()
  const props = renderPanel({ query: 'birds' })

  await user.click(screen.getByRole('button', { name: /show me/i }))

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
  expect(screen.getByText(/searching retiro park/i)).toBeInTheDocument()
})

test('shows the outcome message without a species list', () => {
  renderPanel({
    result: { outcome: 'resolved', message: 'Early preview message' },
  })

  expect(screen.getByText('Early preview message')).toBeInTheDocument()
  expect(screen.queryByRole('list')).not.toBeInTheDocument()
})

test('keeps the form enabled and resubmittable after a resolved result', () => {
  renderPanel({
    result: { outcome: 'resolved', message: 'ok' },
    loading: false,
  })

  expect(screen.getByLabelText('What would you want to see on a walk?')).not.toBeDisabled()
  expect(screen.getByRole('button', { name: /show me/i })).not.toBeDisabled()
})

test('keeps the form enabled and resubmittable after a non-resolved outcome', () => {
  renderPanel({
    result: { outcome: 'unresolved', message: "couldn't match that" },
    loading: false,
  })

  expect(screen.getByLabelText('What would you want to see on a walk?')).not.toBeDisabled()
  expect(screen.getByRole('button', { name: /show me/i })).not.toBeDisabled()
})
