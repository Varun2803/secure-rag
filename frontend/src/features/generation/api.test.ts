import { describe, expect, it, vi } from 'vitest'
import type { AuthenticatedRequest } from '@/features/tenants/api'
import { generateAnswer } from './api'

const tenantId = '11111111-1111-4111-8111-111111111111'
const response = { query: 'leave policy', answer: 'Employees receive leave [S1].', citations: [{ document_id: '22222222-2222-4222-8222-222222222222', chunk_id: '33333333-3333-4333-8333-333333333333', page_numbers: [2], relevance_score: 0.91 }], result_count: 1, grounded: true }

describe('generation API', () => {
  it('sends the tenant-scoped answer request and parses backend citations', async () => {
    const request = vi.fn(async () => response) as unknown as AuthenticatedRequest & ReturnType<typeof vi.fn>
    const controller = new AbortController()
    await expect(generateAnswer(request, tenantId, { query: ' leave policy ', top_k: 4 }, controller.signal)).resolves.toMatchObject(response)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/generation/answer`, { method: 'POST', json: { query: 'leave policy', top_k: 4 }, signal: controller.signal })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('defaults top_k to five and rejects unsupported identity or ACL fields', async () => {
    const request = vi.fn(async () => ({ ...response, citations: [], result_count: 0, grounded: false })) as unknown as AuthenticatedRequest
    await generateAnswer(request, tenantId, { query: 'question' })
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/generation/answer`, expect.objectContaining({ json: { query: 'question', top_k: 5 } }))
    await expect(generateAnswer(request, tenantId, { query: 'question', top_k: 5, role: 'OWNER' } as never)).rejects.toThrow()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('rejects malformed UUIDs and non-boolean grounding flags', async () => {
    const request = vi.fn(async () => ({ ...response, citations: [{ ...response.citations[0], document_id: 'not-a-uuid' }] })) as unknown as AuthenticatedRequest
    await expect(generateAnswer(request, tenantId, { query: 'question', top_k: 5 })).rejects.toThrow('invalid answer response')
    const invalidGrounding = vi.fn(async () => ({ ...response, grounded: 'yes' })) as unknown as AuthenticatedRequest
    await expect(generateAnswer(invalidGrounding, tenantId, { query: 'question', top_k: 5 })).rejects.toThrow('invalid answer response')
  })
})
