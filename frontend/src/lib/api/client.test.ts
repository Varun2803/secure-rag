import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('typed API client foundation', () => {
  it('sends JSON to the relative API base and returns typed JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'healthy' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await apiRequest<{ status: string }>('/client-test/json', { method: 'POST', json: { ping: true } })

    expect(result.status).toBe('healthy')
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/client-test/json', expect.any(Object))
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{"ping":true}')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })

  it('leaves multipart Content-Type to the browser', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const formData = new FormData()
    formData.append('file', new Blob(['pdf']), 'sample.pdf')

    await apiRequest('/client-test/multipart', { method: 'POST', formData })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
    expect(init.body).toBe(formData)
  })

  it('supports URL-encoded fields, bearer injection, and abort signals without persisting credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await apiRequest('/client-test/form', {
      method: 'POST',
      urlEncoded: { first: 'one value', second: 'another' },
      accessToken: 'test-token',
      signal: controller.signal,
    })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(String(init.body)).toContain('first=one+value')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer test-token')
    expect(init.signal).toBe(controller.signal)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('normalizes HTTP errors and retains backend request IDs without exposing response details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'sensitive internal detail' }), {
      status: 403,
      headers: { 'x-request-id': 'request-123' },
    })))

    await expect(apiRequest('/tenants/private')).rejects.toMatchObject({
      kind: 'http',
      status: 403,
      requestId: 'request-123',
      message: 'You do not have permission to perform this action.',
    })
  })

  it.each([
    [401, 'Authentication is required or has expired.'],
    [403, 'You do not have permission to perform this action.'],
    [404, 'The requested resource is unavailable.'],
    [413, 'The request is larger than the server allows.'],
    [422, 'Please review the submitted values.'],
    [503, 'The service is temporarily unavailable.'],
  ])('maps HTTP %i to a sanitized client error', async (status, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"private backend detail"}', { status })))

    await expect(apiRequest('/health')).rejects.toMatchObject({
      kind: 'http',
      status,
      message,
    })
  })

  it('preserves a successful response request ID through the caller callback', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', {
      status: 200,
      headers: { 'x-request-id': 'request-success' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    const onRequestId = vi.fn()

    await apiRequest('/client-test/request-id', { onRequestId })

    expect(onRequestId).toHaveBeenCalledWith('request-success')
  })

  it('normalizes network failures and rejects absolute API destinations', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network offline')))
    await expect(apiRequest('/client-test/network')).rejects.toMatchObject({ kind: 'network' })
    await expect(apiRequest('https://untrusted.example/api')).rejects.toThrow(/relative/)
    await expect(apiRequest('/../private')).rejects.toThrow(/relative/)
  })
})
