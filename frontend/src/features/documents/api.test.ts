import { describe, expect, it, vi } from 'vitest'
import type { AuthenticatedRequest } from '@/features/tenants/api'
import { getDocument, getDocumentStatus, listDocuments, uploadDocument } from './api'

const tenantId = '11111111-1111-4111-8111-111111111111'
const documentId = '22222222-2222-4222-8222-222222222222'
const documentRecord = {
  id: documentId, tenant_id: tenantId, uploaded_by: '33333333-3333-4333-8333-333333333333',
  filename: 'internal-key.pdf', original_filename: 'annual-report.pdf', file_hash: 'a'.repeat(64),
  file_size: 2048, content_type: 'application/pdf', page_count: 4, chunk_count: 7, status: 'COMPLETED',
  classification: 1, is_public: false, allowed_roles: [], allowed_users: [],
  created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z', processing_completed_at: '2026-11-21T10:01:00Z',
}

describe('document API', () => {
  it('parses paginated list responses and uses tenant-scoped pagination paths with AbortSignal', async () => {
    const signal = new AbortController().signal
    const request = vi.fn(async () => ({ items: [documentRecord], total: 51, limit: 50, offset: 50 })) as unknown as AuthenticatedRequest
    const page = await listDocuments(request, tenantId, 50, 50, signal)

    expect(page.items[0]?.original_filename).toBe('annual-report.pdf')
    expect(page.total).toBe(51)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents?limit=50&offset=50`, { signal })
  })

  it('parses document details and rejects malformed API records', async () => {
    const request = vi.fn(async () => documentRecord) as unknown as AuthenticatedRequest
    expect(await getDocument(request, tenantId, documentId)).toEqual(documentRecord)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}`, { signal: undefined })

    const invalidRequest = vi.fn(async () => ({ ...documentRecord, classification: 4 })) as unknown as AuthenticatedRequest
    await expect(getDocument(invalidRequest, tenantId, documentId)).rejects.toThrow('invalid document response')
  })

  it('parses persisted processing status and safely accepts nullable failure detail', async () => {
    const response = { id: documentId, status: 'FAILED', page_count: 0, chunk_count: 0, failure_detail: 'PDF_INVALID' }
    const request = vi.fn(async () => response) as unknown as AuthenticatedRequest
    expect(await getDocumentStatus(request, tenantId, documentId)).toEqual(response)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}/status`, { signal: undefined })
  })

  it('uses FormData for upload without manually setting multipart Content-Type', async () => {
    const request = vi.fn(async (_path: string, options?: { method?: string; formData?: FormData; headers?: HeadersInit }) => {
      expect(options?.method).toBe('POST')
      expect(options?.formData).toBeInstanceOf(FormData)
      expect(options?.formData?.get('file')).toBeInstanceOf(File)
      expect(options?.headers).toBeUndefined()
      return documentRecord
    }) as unknown as AuthenticatedRequest
    const file = new File(['%PDF-1.7'], 'annual-report.pdf', { type: 'application/pdf' })

    expect(await uploadDocument(request, tenantId, file)).toEqual(documentRecord)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/upload`, expect.objectContaining({ method: 'POST', formData: expect.any(FormData) }))
  })
})
