import { render, screen } from '@testing-library/react'
import App from './App'

test('renders the Nature Quest heading', () => {
  render(<App />)

  expect(screen.getByRole('heading', { name: 'Nature Quest' })).toBeInTheDocument()
})
