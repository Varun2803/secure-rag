import { describe, expect, it, vi } from 'vitest'
import type { AuthenticatedRequest } from '@/features/tenants/api'
import { searchDocuments } from './api'

const tenantId = '11111111-1111-4111-8111-111111111111'
const response = { query: 'policy', tenant_id: tenantId, results: [{ chunk_id: '22222222-2222-4222-8222-222222222222', document_id: '33333333-3333-4333-8333-333333333333', chunk_index: 0, page_numbers: [1, 2], text: 'Policy excerpt', similarity_score: 0.89 }], result_count: 1 }

describe('retrieval API', () => {
  it('sends the tenant-scoped POST request with query and top_k and parses results', async () => {
    const request = vi.fn(async () => response) as unknown as AuthenticatedRequest & ReturnType<typeof vi.fn>
    const controller = new AbortController()
    await expect(searchDocuments(request, tenantId, { query: ' policy ', top_k: 5 }, controller.signal)).resolves.toMatchObject({ ...response, query: 'policy' })
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/retrieval/search`, { method: 'POST', json: { query: 'policy', top_k: 5 }, signal: controller.signal })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('defaults top_k to five when omitted', async () => {
    const request = vi.fn(async () => ({ ...response, results: [], result_count: 0 })) as unknown as AuthenticatedRequest & ReturnType<typeof vi.fn>
    await searchDocuments(request, tenantId, { query: 'policy' })
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/retrieval/search`, expect.objectContaining({ json: { query: 'policy', top_k: 5 } }))
  })

  it.each([{ query: '  ', top_k: 1 }, { query: 'x'.repeat(2001), top_k: 1 }, { query: 'ok', top_k: 0 }, { query: 'ok', top_k: 21 }, { query: 'ok', top_k: 1, tenant_id: tenantId }])('rejects invalid or unsupported request fields before fetch', async (input) => {
    const request = vi.fn() as unknown as AuthenticatedRequest
    await expect(searchDocuments(request, tenantId, input)).rejects.toThrow()
    expect(request).not.toHaveBeenCalled()
  })

  it('rejects invalid response shapes and preserves authenticated-client errors', async () => {
    const request = vi.fn(async () => ({ ...response, tenant_id: 'invalid-uuid' })) as unknown as AuthenticatedRequest
    await expect(searchDocuments(request, tenantId, { query: 'policy', top_k: 5 })).rejects.toThrow('invalid retrieval response')
    const failing = vi.fn(async () => { throw new Error('API 503') }) as unknown as AuthenticatedRequest
    await expect(searchDocuments(failing, tenantId, { query: 'policy', top_k: 5 })).rejects.toThrow('API 503')
  })

  it('rejects a response whose tenant does not match the requested workspace', async () => {
    const request = vi.fn(async () => ({ ...response, tenant_id: '99999999-9999-4999-8999-999999999999' })) as unknown as AuthenticatedRequest
    await expect(searchDocuments(request, tenantId, { query: 'policy', top_k: 5 })).rejects.toThrow('invalid retrieval response')
  })
})
