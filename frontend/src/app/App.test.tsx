import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { AppProviders } from './providers/app-providers'

const tokenResponse = { access_token: 'test-access-token', token_type: 'bearer', expires_in: 1800 }
const userResponse = {
  id: 'f062ad64-7cc0-471d-9a95-03eeed425b7a',
  email: 'user@example.test',
  full_name: 'Example User',
  is_active: true,
  is_superuser: false,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
}

function renderApp() {
  return render(<AppProviders><App /></AppProviders>)
}

function successfulLoginFetch() {
  return vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(tokenResponse), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(userResponse), { status: 200 }))
    .mockResolvedValueOnce(new Response('[]', { status: 200 }))
}

beforeEach(() => {
  window.history.replaceState({}, '', '/')
  document.documentElement.classList.remove('dark')
})

afterEach(() => cleanup())

describe('authentication routes and screens', () => {
  it('redirects an unauthenticated visitor to login without rendering protected content', async () => {
    renderApp()

    expect(await screen.findByRole('heading', { name: 'Sign in to SecureRAG' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Tenant workspace' })).not.toBeInTheDocument()
  })

  it('switches the accessible theme control between light and dark modes on auth routes', async () => {
    renderApp()
    const themeButton = screen.getByRole('button', { name: /switch to dark theme/i })
    fireEvent.click(themeButton)

    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: /switch to light theme/i })).toBeInTheDocument()
  })

  it('validates required login fields and displays a generic credential error', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"detail":"sensitive login failure"}', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderApp()

    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Email or password is incorrect.')
    expect(screen.queryByText('sensitive login failure')).not.toBeInTheDocument()
  })

  it('registers without automatically authenticating and then routes to login', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(userResponse), { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderApp()

    await user.click(screen.getByRole('link', { name: 'Create an account' }))
    expect(await screen.findByRole('heading', { name: 'Create your account' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Full name'), ' Example User ')
    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'SecurePassword123!')
    await user.type(screen.getByLabelText('Confirm password'), 'SecurePassword123!')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to SecureRAG' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your account is ready. Sign in to continue.')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/register', expect.any(Object))
  })

  it('validates registration password length and confirmation', async () => {
    const user = userEvent.setup()
    renderApp()
    await user.click(screen.getByRole('link', { name: 'Create an account' }))
    await screen.findByRole('heading', { name: 'Create your account' })
    await user.type(screen.getByLabelText('Full name'), 'Example User')
    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'short')
    await user.type(screen.getByLabelText('Confirm password'), 'different')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Use at least 12 characters.')).toBeInTheDocument()
    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument()
  })

  it('shows duplicate email feedback without exposing backend details', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"detail":"duplicate constraint users.email"}', { status: 409 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderApp()
    await user.click(screen.getByRole('link', { name: 'Create an account' }))
    await screen.findByRole('heading', { name: 'Create your account' })
    await user.type(screen.getByLabelText('Full name'), 'Example User')
    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'SecurePassword123!')
    await user.type(screen.getByLabelText('Confirm password'), 'SecurePassword123!')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('An account with this email already exists.')
    expect(screen.queryByText('duplicate constraint users.email')).not.toBeInTheDocument()
  })

  it('loads the authoritative profile before showing protected content and signs out locally', async () => {
    const fetchMock = successfulLoginFetch()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderApp()
    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'SecurePassword123!')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('heading', { name: 'Tenant workspace' })).toBeInTheDocument()
    expect(await screen.findByText('You do not belong to an active tenant yet. Create one below or ask an existing tenant OWNER to add your account.')).toBeInTheDocument()
    expect(screen.getByText('Example User')).toBeInTheDocument()
    expect(screen.queryByText(/superuser/i)).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(3)
    await user.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to SecureRAG' })).toBeInTheDocument()
    expect(await screen.findByText('You signed out on this device. The issued access token remains valid until its expiry.')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('registers document and knowledge routes and handles navigation when no tenant is active', async () => {
    const fetchMock = successfulLoginFetch()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderApp()
    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'SecurePassword123!')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await screen.findByRole('heading', { name: 'Tenant workspace' })

    const mobileNavigation = screen.getByRole('navigation', { name: 'Mobile workspace navigation' })
    expect(screen.getAllByRole('link', { name: 'Knowledge Search' })).toHaveLength(2)
    await user.click(within(mobileNavigation).getByRole('link', { name: 'Documents' }))
    expect(await screen.findByRole('heading', { name: 'Documents' })).toBeInTheDocument()
    expect(screen.getByText('Select an active tenant workspace to view its documents.')).toBeInTheDocument()
    await user.click(within(mobileNavigation).getByRole('link', { name: 'Knowledge Search' }))
    expect(await screen.findByRole('heading', { name: 'Knowledge Search' })).toBeInTheDocument()
    expect(screen.getByText('Select an active tenant workspace to search its authorized documents.')).toBeInTheDocument()
  })

  it('redirects authenticated visitors away from login and registration', async () => {
    const fetchMock = successfulLoginFetch()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderApp()
    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'SecurePassword123!')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await screen.findByRole('heading', { name: 'Tenant workspace' })

    for (const publicPath of ['/login', '/register']) {
      await act(async () => {
        window.history.pushState({}, '', publicPath)
        window.dispatchEvent(new PopStateEvent('popstate'))
      })
      expect(await screen.findByRole('heading', { name: 'Tenant workspace' })).toBeInTheDocument()
      expect(window.location.pathname).toBe('/')
    }
  })

  it('keeps protected content hidden while the current profile is loading', async () => {
    let releaseProfile!: (response: Response) => void
    const profilePromise = new Promise<Response>((resolve) => { releaseProfile = resolve })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(tokenResponse), { status: 200 }))
      .mockReturnValueOnce(profilePromise)
      .mockResolvedValueOnce(new Response('[]', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderApp()
    await user.type(screen.getByLabelText('Email'), 'user@example.test')
    await user.type(screen.getByLabelText('Password'), 'SecurePassword123!')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Completing sign-in')
    expect(screen.queryByRole('heading', { name: 'Tenant workspace' })).not.toBeInTheDocument()

    releaseProfile(new Response(JSON.stringify(userResponse), { status: 200 }))
    expect(await screen.findByRole('heading', { name: 'Tenant workspace' })).toBeInTheDocument()
  })

})
