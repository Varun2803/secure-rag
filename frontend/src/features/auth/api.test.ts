import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchCurrentUser, loginUser, registerUser } from './api'

afterEach(() => vi.unstubAllGlobals())

const userResponse = {
  id: 'f062ad64-7cc0-471d-9a95-03eeed425b7a',
  email: 'user@example.test',
  full_name: 'Example User',
  is_active: true,
  is_superuser: false,
  created_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
}

describe('authentication API contract', () => {
  it('registers with JSON and returns the safe public profile without authenticating', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(userResponse), { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)

    const user = await registerUser({ email: 'user@example.test', password: 'SecurePassword123!', full_name: 'Example User' })

    expect(user).toMatchObject({ id: userResponse.id, email: userResponse.email, full_name: userResponse.full_name })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/register', expect.any(Object))
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ email: 'user@example.test', password: 'SecurePassword123!', full_name: 'Example User' }))
    expect(new Headers(init.headers).has('Authorization')).toBe(false)
  })

  it('sends login credentials as URL-encoded OAuth2 form fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      access_token: 'in-memory-test-token', token_type: 'bearer', expires_in: 1800,
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const response = await loginUser('user@example.test', 'SecurePassword123!')

    expect(response).toMatchObject({ token_type: 'bearer', expires_in: 1800 })
    expect(response.access_token).toBe('in-memory-test-token')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/login', expect.any(Object))
    expect(new Headers(init.headers).get('Content-Type')).toContain('application/x-www-form-urlencoded')
    expect(String(init.body)).toContain('username=user%40example.test')
    expect(String(init.body)).toContain('password=SecurePassword123%21')
    expect(new Headers(init.headers).has('Authorization')).toBe(false)
  })

  it('loads the current profile with a bearer token through the central API client', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(userResponse), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const user = await fetchCurrentUser('in-memory-test-token')

    expect(user.email).toBe('user@example.test')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me', expect.any(Object))
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer in-memory-test-token')
  })

  it('does not expose raw error response details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"sensitive backend internals"}', { status: 503 })))

    await expect(loginUser('user@example.test', 'secret-password'))
      .rejects.toMatchObject({ status: 503, message: 'The service is temporarily unavailable.' })
  })
})
