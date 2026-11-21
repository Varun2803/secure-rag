import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '@/app/providers/theme-provider'
import { useAuth } from './auth-context'
import { AuthProvider } from './auth-provider'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const tokenResponse = { access_token: 'ephemeral-test-token', token_type: 'bearer', expires_in: 1800 }
const userResponse = {
  id: 'f062ad64-7cc0-471d-9a95-03eeed425b7a',
  email: 'user@example.test',
  full_name: 'Example User',
  is_active: true,
  is_superuser: false,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
}

function Harness() {
  const auth = useAuth()
  const queryClient = useQueryClient()

  return (
    <div>
      <output data-testid="auth-status">{auth.status}</output>
      <output data-testid="profile">{auth.user?.full_name ?? 'no profile'}</output>
      {auth.error && <p role="alert">{auth.error}</p>}
      <button onClick={() => void auth.login('user@example.test', 'SecurePassword123!')}>Login test user</button>
      <button onClick={() => void auth.authenticatedRequest('/auth/me').catch(() => undefined)}>Make authenticated request</button>
      <button onClick={() => queryClient.setQueryData(['private-data'], 'cached value')}>Seed query cache</button>
      <button onClick={auth.logout}>Logout locally</button>
      <output data-testid="cached-value">{String(queryClient.getQueryData(['private-data']) ?? 'empty')}</output>
    </div>
  )
}

function renderHarness() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <AuthProvider><Harness /></AuthProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </BrowserRouter>,
  )
  return { ...view, queryClient }
}

describe('authentication state', () => {
  it('fetches the authoritative profile before marking the session authenticated', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(tokenResponse), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(userResponse), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderHarness()

    await user.click(screen.getByRole('button', { name: 'Login test user' }))

    expect(await screen.findByTestId('auth-status')).toHaveTextContent('authenticated')
    expect(screen.getByTestId('profile')).toHaveTextContent('Example User')
    const [, profileRequest] = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(new Headers(profileRequest.headers).get('Authorization')).toBe('Bearer ephemeral-test-token')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('clears the provisional token and remains unauthenticated when profile loading fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(tokenResponse), { status: 200 }))
      .mockResolvedValueOnce(new Response('{"detail":"private error"}', { status: 503 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderHarness()

    await user.click(screen.getByRole('button', { name: 'Login test user' }))

    expect(await screen.findByTestId('auth-status')).toHaveTextContent('unauthenticated')
    expect(screen.getByTestId('profile')).toHaveTextContent('no profile')
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign-in could not be completed. Please try again.')
    await user.click(screen.getByRole('button', { name: 'Make authenticated request' }))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('expires locally on the configured timer and clears the query cache', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...tokenResponse, expires_in: 1 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(userResponse), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const { queryClient } = renderHarness()

    fireEvent.click(screen.getByRole('button', { name: 'Login test user' }))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByTestId('auth-status')).toHaveTextContent('authenticated')
    fireEvent.click(screen.getByRole('button', { name: 'Seed query cache' }))
    expect(queryClient.getQueryData(['private-data'])).toBe('cached value')

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByTestId('auth-status')).toHaveTextContent('expired')
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('measures token expiry from the login response while profile verification is delayed', async () => {
    vi.useFakeTimers()
    let loginResponseAt = 0
    let releaseProfile!: (response: Response) => void
    const profilePromise = new Promise<Response>((resolve) => { releaseProfile = resolve })
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith('/auth/login')) {
        loginResponseAt = Date.now()
        return Promise.resolve(new Response(JSON.stringify({ ...tokenResponse, expires_in: 10 }), { status: 200 }))
      }
      return profilePromise
    })
    vi.stubGlobal('fetch', fetchMock)
    renderHarness()

    fireEvent.click(screen.getByRole('button', { name: 'Login test user' }))
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByTestId('auth-status')).toHaveTextContent('authenticating')
    expect(loginResponseAt).toBeGreaterThan(0)

    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    releaseProfile(new Response(JSON.stringify(userResponse), { status: 200 }))
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByTestId('auth-status')).toHaveTextContent('authenticated')

    await act(async () => { await vi.advanceTimersByTimeAsync(3999) })
    expect(screen.getByTestId('auth-status')).toHaveTextContent('authenticated')
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(screen.getByTestId('auth-status')).toHaveTextContent('expired')
  })

  it('does not establish a session when the token expires during profile verification', async () => {
    vi.useFakeTimers()
    let releaseProfile!: (response: Response) => void
    const profilePromise = new Promise<Response>((resolve) => { releaseProfile = resolve })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...tokenResponse, expires_in: 5 }), { status: 200 }))
      .mockReturnValueOnce(profilePromise)
    vi.stubGlobal('fetch', fetchMock)
    const { queryClient } = renderHarness()
    queryClient.setQueryData(['private-data'], 'cached value')

    fireEvent.click(screen.getByRole('button', { name: 'Login test user' }))
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByTestId('auth-status')).toHaveTextContent('authenticating')

    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    releaseProfile(new Response(JSON.stringify(userResponse), { status: 200 }))
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })

    expect(screen.getByTestId('auth-status')).toHaveTextContent('expired')
    expect(screen.getByTestId('profile')).toHaveTextContent('no profile')
    expect(queryClient.getQueryData(['private-data'])).toBeUndefined()
  })

  it('clears the token, user, and query cache on local logout', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(tokenResponse), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(userResponse), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    const { queryClient } = renderHarness()

    await user.click(screen.getByRole('button', { name: 'Login test user' }))
    expect(await screen.findByTestId('auth-status')).toHaveTextContent('authenticated')
    await user.click(screen.getByRole('button', { name: 'Seed query cache' }))
    await user.click(screen.getByRole('button', { name: 'Logout locally' }))

    expect(screen.getByTestId('auth-status')).toHaveTextContent('unauthenticated')
    expect(screen.getByTestId('profile')).toHaveTextContent('no profile')
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
  })

  it('expires authentication when an authenticated request receives 401', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(tokenResponse), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(userResponse), { status: 200 }))
      .mockResolvedValueOnce(new Response('{"detail":"internal detail"}', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderHarness()

    await user.click(screen.getByRole('button', { name: 'Login test user' }))
    expect(await screen.findByTestId('auth-status')).toHaveTextContent('authenticated')
    await user.click(screen.getByRole('button', { name: 'Make authenticated request' }))

    expect(await screen.findByTestId('auth-status')).toHaveTextContent('expired')
    expect(screen.getByTestId('profile')).toHaveTextContent('no profile')
  })

  it('starts unauthenticated on a fresh provider mount instead of restoring browser storage', () => {
    localStorage.setItem('access_token', 'must-not-restore')
    sessionStorage.setItem('access_token', 'must-not-restore')
    renderHarness()

    expect(screen.getByTestId('auth-status')).toHaveTextContent('unauthenticated')
    expect(screen.getByTestId('profile')).toHaveTextContent('no profile')
    localStorage.clear()
    sessionStorage.clear()
  })
})
