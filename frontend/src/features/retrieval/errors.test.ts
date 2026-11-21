import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api/api-error'
import { safeKnowledgeError } from './errors'

describe('safe knowledge errors', () => {
  it.each([
    [401, 'sign-in is required'],
    [403, 'do not have access'],
    [404, 'workspace is unavailable'],
    [422, 'query or result count is invalid'],
    [500, 'could not complete this request'],
    [503, 'temporarily unavailable'],
  ] as const)('maps HTTP %i to safe user-facing guidance', (status, message) => {
    const error = new ApiError('sensitive database or provider detail', 'http', status, 'request-123')
    const safe = safeKnowledgeError(error, status === 503 ? 'answer' : 'search')
    expect(safe).toContain(message)
    expect(safe).toContain('request-123')
    expect(safe).not.toContain('sensitive database or provider detail')
  })
})
