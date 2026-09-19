/* AuthGate must be invisible in the default setup and correct in the shared one. */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuthGate from './AuthGate.jsx'
import * as api from '../api.js'

vi.mock('../api.js', () => ({
  getAuthStatus: vi.fn(),
  openAuthSession: vi.fn(),
}))

beforeEach(() => vi.clearAllMocks())

describe('AuthGate', () => {
  it('renders the app untouched when no token is required', async () => {
    api.getAuthStatus.mockResolvedValue({ required: false, authenticated: true })
    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() => expect(screen.getByText('the app')).toBeInTheDocument())
    expect(screen.queryByPlaceholderText('Access token')).not.toBeInTheDocument()
  })

  it('renders the app when a cookie already authenticates the browser', async () => {
    // A returning user must not be asked again after a reload.
    api.getAuthStatus.mockResolvedValue({ required: true, authenticated: true })
    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() => expect(screen.getByText('the app')).toBeInTheDocument())
  })

  it('asks for the token when one is required', async () => {
    api.getAuthStatus.mockResolvedValue({ required: true, authenticated: false })
    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() =>
      expect(screen.getByPlaceholderText('Access token')).toBeInTheDocument())
    expect(screen.queryByText('the app')).not.toBeInTheDocument()
  })

  it('exchanges the token and then reveals the app', async () => {
    const user = userEvent.setup()
    api.getAuthStatus.mockResolvedValue({ required: true, authenticated: false })
    api.openAuthSession.mockResolvedValue({ required: true, authenticated: true })

    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() =>
      expect(screen.getByPlaceholderText('Access token')).toBeInTheDocument())

    await user.type(screen.getByPlaceholderText('Access token'), 'secret-token')
    await user.click(screen.getByRole('button', { name: /Continue/ }))

    await waitFor(() => expect(screen.getByText('the app')).toBeInTheDocument())
    expect(api.openAuthSession).toHaveBeenCalledWith('secret-token')
  })

  it('keeps the gate up and explains a rejected token', async () => {
    const user = userEvent.setup()
    api.getAuthStatus.mockResolvedValue({ required: true, authenticated: false })
    api.openAuthSession.mockRejectedValue(new Error('That token was not accepted.'))

    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() =>
      expect(screen.getByPlaceholderText('Access token')).toBeInTheDocument())

    await user.type(screen.getByPlaceholderText('Access token'), 'wrong')
    await user.click(screen.getByRole('button', { name: /Continue/ }))

    await waitFor(() =>
      expect(screen.getByText(/was not accepted/)).toBeInTheDocument())
    expect(screen.queryByText('the app')).not.toBeInTheDocument()
  })

  it('says the backend is unreachable rather than asking for a token', async () => {
    // Failing into a token prompt would be actively misleading: no token
    // fixes a backend that is not running.
    api.getAuthStatus.mockRejectedValue(new Error('Could not reach the LANA backend.'))
    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() =>
      expect(screen.getByText(/Can't reach LANA/)).toBeInTheDocument())
    expect(screen.queryByPlaceholderText('Access token')).not.toBeInTheDocument()
  })

  it('renders nothing while the check is in flight', () => {
    api.getAuthStatus.mockReturnValue(new Promise(() => {}))
    const { container } = render(<AuthGate><div>the app</div></AuthGate>)
    // No flash of either the app or the gate before the answer arrives.
    expect(container).toBeEmptyDOMElement()
  })
})
