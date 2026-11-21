import { ApiError } from './api-error'

export const API_BASE_PATH = '/api/v1'

export type ApiRequestOptions = Omit<RequestInit, 'body' | 'headers'> & {
  headers?: HeadersInit
  json?: unknown
  formData?: FormData
  urlEncoded?: URLSearchParams | Record<string, string>
  accessToken?: string
  notifyUnauthorized?: boolean
  onRequestId?: (requestId: string) => void
}

type UnauthorizedHandler = (accessToken: string, requestId?: string) => void
const unauthorizedHandlers = new Set<UnauthorizedHandler>()

export function subscribeToUnauthorized(handler: UnauthorizedHandler) {
  unauthorizedHandlers.add(handler)
  return () => { unauthorizedHandlers.delete(handler) }
}

function requestBody(options: ApiRequestOptions, headers: Headers): BodyInit | undefined {
  const provided = [options.json !== undefined, options.formData !== undefined, options.urlEncoded !== undefined].filter(Boolean).length
  if (provided > 1) throw new TypeError('Choose only one request body format')

  if (options.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    return JSON.stringify(options.json)
  }
  if (options.formData !== undefined) return options.formData
  if (options.urlEncoded !== undefined) {
    headers.set('Content-Type', 'application/x-www-form-urlencoded;charset=UTF-8')
    return options.urlEncoded instanceof URLSearchParams
      ? options.urlEncoded
      : new URLSearchParams(options.urlEncoded)
  }
  return undefined
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const segments = path.split(/[/?#]/).filter(Boolean)
  const hasTraversalSegment = (() => {
    try {
      return segments.some((segment) => ['.', '..'].includes(decodeURIComponent(segment)))
    } catch {
      throw new TypeError('API path contains malformed encoding')
    }
  })()
  if (!path.startsWith('/') || path.startsWith('//') || /^https?:/i.test(path) || path.includes('\\') || hasTraversalSegment) {
    throw new TypeError('API paths must be relative to the configured API base path')
  }

  const headers = new Headers(options.headers)
  if (options.accessToken) headers.set('Authorization', `Bearer ${options.accessToken}`)
  const body = requestBody(options, headers)
  const requestOptions: RequestInit = { ...options, headers, body }
  delete (requestOptions as RequestInit & { json?: unknown }).json
  delete (requestOptions as RequestInit & { formData?: FormData }).formData
  delete (requestOptions as RequestInit & { urlEncoded?: URLSearchParams | Record<string, string> }).urlEncoded
  delete (requestOptions as RequestInit & { accessToken?: string }).accessToken
  delete (requestOptions as RequestInit & { notifyUnauthorized?: boolean }).notifyUnauthorized
  delete (requestOptions as RequestInit & { onRequestId?: (requestId: string) => void }).onRequestId

  let response: Response
  try {
    response = await fetch(`${API_BASE_PATH}${path}`, { ...requestOptions, headers, body })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('Unable to reach the service. Check your connection and try again.', 'network')
  }

  const requestId = response.headers.get('x-request-id') ?? undefined
  if (requestId) options.onRequestId?.(requestId)
  if (response.status === 401 && options.accessToken && options.notifyUnauthorized !== false) {
    for (const handler of unauthorizedHandlers) handler(options.accessToken, requestId)
  }
  if (!response.ok) throw ApiError.fromResponse(response.status, requestId)
  if (response.status === 204) return undefined as T

  try {
    return await response.json() as T
  } catch {
    throw new ApiError('The service returned an invalid response.', 'invalid-response', response.status, requestId)
  }
}
