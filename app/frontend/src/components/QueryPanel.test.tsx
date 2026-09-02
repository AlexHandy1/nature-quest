import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect } from 'vitest'
import QueryPanel from './QueryPanel'

function baseProps(overrides = {}) {
  return {
    query: '',
    onQueryChange: vi.fn(),
    loading: false,
    result: null,
    onSubmit: vi.fn((event) => event.preventDefault()),
    ...overrides,
  }
}

function renderPanel(overrides = {}) {
  const props = baseProps(overrides)
  render(<QueryPanel {...props} />)
  return props
}

function renderPanelForRerender(overrides = {}) {
  const props = baseProps(overrides)
  const utils = render(<QueryPanel {...props} />)
  return {
    ...utils,
    rerender: (next = {}) => utils.rerender(<QueryPanel {...baseProps({ ...overrides, ...next })} />),
  }
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

test('disables the input and button and names GBIF in the loading message', () => {
  renderPanel({ loading: true })

  expect(screen.getByRole('button', { name: /searching/i })).toBeDisabled()
  expect(screen.getByLabelText('What would you want to see on a walk?')).toBeDisabled()
  expect(screen.getByText(/searching gbif/i)).toBeInTheDocument()
})

test('escalates the loading message when GBIF is slow to respond', () => {
  vi.useFakeTimers()
  try {
    renderPanel({ loading: true })
    expect(screen.getByText(/searching gbif/i)).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(6000)
    })

    expect(screen.getByText(/gbif.*slow to respond/i)).toBeInTheDocument()
    expect(screen.queryByText(/searching gbif for species/i)).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('does not show the slow-GBIF message before the 6s threshold', () => {
  vi.useFakeTimers()
  try {
    renderPanel({ loading: true })

    act(() => {
      vi.advanceTimersByTime(5999)
    })
    expect(screen.getByText(/searching gbif for species/i)).toBeInTheDocument()
    expect(screen.queryByText(/slow to respond/i)).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(screen.getByText(/slow to respond/i)).toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('clears the slow-GBIF message the moment a query resolves after it had escalated', () => {
  vi.useFakeTimers()
  try {
    const { rerender } = renderPanelForRerender({ loading: true })

    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.getByText(/slow to respond/i)).toBeInTheDocument()

    rerender({ loading: false, result: { outcome: 'resolved', message: 'ok' } })

    // gone immediately on resolve — no extra timer tick needed
    expect(screen.queryByText(/slow to respond/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/searching gbif/i)).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('resets the slow-GBIF message once loading finishes', () => {
  vi.useFakeTimers()
  try {
    const { rerender } = renderPanelForRerender({ loading: true })
    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.getByText(/gbif.*slow to respond/i)).toBeInTheDocument()

    rerender({ loading: false })
    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.queryByText(/slow to respond/i)).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('shows the outcome message for a non-resolved outcome', () => {
  renderPanel({
    result: { outcome: 'unresolved', message: "couldn't match that" },
  })

  expect(screen.getByText("couldn't match that")).toBeInTheDocument()
  expect(screen.queryByRole('list')).not.toBeInTheDocument()
})

test('the gbif_unavailable outcome links to GBIF status and mentions a backup plan', () => {
  renderPanel({
    result: {
      outcome: 'gbif_unavailable',
      message: 'Our species data comes from GBIF, and their service is responding slowly.',
    },
  })

  expect(screen.getByText(/our species data comes from gbif/i)).toBeInTheDocument()

  const link = screen.getByRole('link', { name: /gbif.*status/i })
  expect(link).toHaveAttribute('href', 'https://status.gbif.org')
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))

  expect(screen.getByText(/backup data sources/i)).toBeInTheDocument()
})

test('the gbif_unavailable message renders exactly once, not duplicated by the generic branch', () => {
  renderPanel({
    result: {
      outcome: 'gbif_unavailable',
      message: 'GBIF is responding slowly.',
    },
  })

  expect(screen.getAllByText(/gbif is responding slowly/i)).toHaveLength(1)
})

test('the gbif_unavailable block still carries the error styling', () => {
  renderPanel({
    result: { outcome: 'gbif_unavailable', message: 'GBIF is slow.' },
  })

  expect(screen.getByText(/gbif is slow/i).closest('p')).toHaveClass('form-message--error')
})

test.each([
  ['rate_limited', 'Slow down a moment.'],
  ['daily_limit_reached', "That's all for today."],
  ['unresolved', "Couldn't match that."],
  ['no_results', 'Nothing found here.'],
])('the %s outcome renders its plain message with no GBIF status link', (outcome, message) => {
  renderPanel({ result: { outcome, message } })

  expect(screen.getByText(message)).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /gbif.*status/i })).not.toBeInTheDocument()
  expect(screen.queryByText(/backup data sources/i)).not.toBeInTheDocument()
})

test('a stale gbif_unavailable result is hidden while a new query is loading', () => {
  renderPanel({
    loading: true,
    result: { outcome: 'gbif_unavailable', message: 'GBIF was slow last time.' },
  })

  expect(screen.getByText(/searching gbif for species/i)).toBeInTheDocument()
  expect(screen.queryByText(/gbif was slow last time/i)).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /gbif.*status/i })).not.toBeInTheDocument()
})

test('does not escalate to the slow-GBIF message when loading ends before the threshold', () => {
  vi.useFakeTimers()
  try {
    const { rerender } = renderPanelForRerender({ loading: true })

    act(() => {
      vi.advanceTimersByTime(3000)
    })
    rerender({ loading: false, result: { outcome: 'resolved', message: 'ok' } })
    act(() => {
      vi.advanceTimersByTime(6000)
    })

    expect(screen.queryByText(/slow to respond/i)).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('a second query restarts the slow-GBIF timer from zero', () => {
  vi.useFakeTimers()
  try {
    const { rerender } = renderPanelForRerender({ loading: true })
    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.getByText(/slow to respond/i)).toBeInTheDocument()

    // result comes back, then the user submits again
    rerender({ loading: false, result: { outcome: 'gbif_unavailable', message: 'slow' } })
    rerender({ loading: true, result: { outcome: 'gbif_unavailable', message: 'slow' } })

    expect(screen.getByText(/searching gbif for species/i)).toBeInTheDocument()
    expect(screen.queryByText(/slow to respond/i)).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(5999)
    })
    expect(screen.queryByText(/slow to respond/i)).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(screen.getByText(/slow to respond/i)).toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('unmounting mid-load clears the slow-GBIF timer without error', () => {
  vi.useFakeTimers()
  const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  try {
    const { unmount } = renderPanelForRerender({ loading: true })
    unmount()
    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(errorSpy).not.toHaveBeenCalled()
  } finally {
    errorSpy.mockRestore()
    vi.useRealTimers()
  }
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
