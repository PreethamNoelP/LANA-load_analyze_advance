/* AuthGate must be invisible in the default setup and correct in the shared one. */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuthGate from './AuthGate.jsx'
import * as api from '../api.js'

vi.mock('../api.js', () => ({
  getAuthStatus: vi.fn(),
  openAuthSession: vi.fn(),
  login: vi.fn(),
  requestPasswordReset: vi.fn(),
  resetPassword: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  window.history.pushState({}, '', '/')
})

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

  it('shows the accounts sign-in form and a forgot-password link', async () => {
    api.getAuthStatus.mockResolvedValue({ mode: 'accounts', required: true, authenticated: false })
    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() => expect(screen.getByPlaceholderText('Username')).toBeInTheDocument())
    expect(screen.getByText('Forgot password?')).toBeInTheDocument()
  })

  it('requests a reset link and shows the server response', async () => {
    const user = userEvent.setup()
    api.getAuthStatus.mockResolvedValue({ mode: 'accounts', required: true, authenticated: false })
    api.requestPasswordReset.mockResolvedValue({
      detail: 'If that account exists, a reset link has been sent to it.',
    })

    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() => expect(screen.getByPlaceholderText('Username')).toBeInTheDocument())

    await user.click(screen.getByText('Forgot password?'))
    await user.type(screen.getByPlaceholderText('Username'), 'alice')
    await user.click(screen.getByRole('button', { name: /Send reset link/ }))

    await waitFor(() =>
      expect(screen.getByText(/reset link has been sent/)).toBeInTheDocument())
    expect(api.requestPasswordReset).toHaveBeenCalledWith('alice')
  })

  it('returns to sign-in from the forgot-password view', async () => {
    const user = userEvent.setup()
    api.getAuthStatus.mockResolvedValue({ mode: 'accounts', required: true, authenticated: false })

    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() => expect(screen.getByPlaceholderText('Username')).toBeInTheDocument())

    await user.click(screen.getByText('Forgot password?'))
    await waitFor(() => expect(screen.getByText('Reset your password')).toBeInTheDocument())

    await user.click(screen.getByText('Back to sign in'))
    await waitFor(() => expect(screen.getByText('Sign in to LANA')).toBeInTheDocument())
  })

  it('shows the reset-password form when the URL carries a token', async () => {
    window.history.pushState({}, '', '/?token=abc123')
    api.getAuthStatus.mockResolvedValue({ mode: 'accounts', required: true, authenticated: false })

    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() =>
      expect(screen.getByText('Set a new password')).toBeInTheDocument())
    // The token must not linger in the visible URL once read.
    expect(window.location.search).toBe('')
  })

  it('submits the new password with the token from the URL', async () => {
    const user = userEvent.setup()
    window.history.pushState({}, '', '/?token=abc123')
    api.getAuthStatus.mockResolvedValue({ mode: 'accounts', required: true, authenticated: false })
    api.resetPassword.mockResolvedValue({ detail: 'ok' })

    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() =>
      expect(screen.getByPlaceholderText('New password')).toBeInTheDocument())

    await user.type(screen.getByPlaceholderText('New password'), 'a-brand-new-password')
    await user.click(screen.getByRole('button', { name: /Set new password/ }))

    await waitFor(() =>
      expect(screen.getByText(/password has been changed/)).toBeInTheDocument())
    expect(api.resetPassword).toHaveBeenCalledWith('abc123', 'a-brand-new-password')
  })

  it('shows an error for an invalid or expired reset token', async () => {
    const user = userEvent.setup()
    window.history.pushState({}, '', '/?token=stale')
    api.getAuthStatus.mockResolvedValue({ mode: 'accounts', required: true, authenticated: false })
    api.resetPassword.mockRejectedValue(new Error('That reset link is invalid or has expired.'))

    render(<AuthGate><div>the app</div></AuthGate>)
    await waitFor(() =>
      expect(screen.getByPlaceholderText('New password')).toBeInTheDocument())

    await user.type(screen.getByPlaceholderText('New password'), 'a-brand-new-password')
    await user.click(screen.getByRole('button', { name: /Set new password/ }))

    await waitFor(() =>
      expect(screen.getByText(/invalid or has expired/)).toBeInTheDocument())
  })
})
