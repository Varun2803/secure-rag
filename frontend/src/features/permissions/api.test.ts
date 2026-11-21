import { describe, expect, it, vi } from 'vitest'
import { getDocumentPermissions, replaceDocumentPermissions } from './api'
import type { AuthenticatedRequest } from '@/features/tenants/api'

const tenantId = '11111111-1111-4111-8111-111111111111'
const documentId = '22222222-2222-4222-8222-222222222222'
const userId = '33333333-3333-4333-8333-333333333333'
const response = { document_id: documentId, tenant_id: tenantId, classification: 'CONFIDENTIAL', is_public: false, allowed_roles: ['HR'], allowed_users: [userId], permission_updated_at: '2026-11-21T10:00:00Z', synchronization_status: 'PENDING' }

describe('document permission API', () => {
  it('uses the tenant and document scoped GET path and forwards AbortSignal', async () => {
    const request = vi.fn(async () => response) as unknown as AuthenticatedRequest & ReturnType<typeof vi.fn>
    const controller = new AbortController()
    await expect(getDocumentPermissions(request, tenantId, documentId, controller.signal)).resolves.toMatchObject(response)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}/permissions`, { signal: controller.signal })
  })

  it('sends the complete ACL replacement to the supported PUT path', async () => {
    const request = vi.fn(async () => response) as unknown as AuthenticatedRequest & ReturnType<typeof vi.fn>
    const body = { classification: 'INTERNAL' as const, is_public: true, allowed_roles: ['HR' as const, 'MANAGER' as const], allowed_users: [userId] }
    await replaceDocumentPermissions(request, tenantId, documentId, body)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}/permissions`, { method: 'PUT', json: body })
  })

  it('rejects invalid service responses and duplicate ACL values before sending', async () => {
    const request = vi.fn(async () => ({ ...response, synchronization_status: 'RETRYING' })) as unknown as AuthenticatedRequest & ReturnType<typeof vi.fn>
    await expect(getDocumentPermissions(request, tenantId, documentId)).rejects.toThrow('invalid permission response')
    await expect(replaceDocumentPermissions(request, tenantId, documentId, { classification: 'INTERNAL', is_public: false, allowed_roles: ['HR', 'HR'], allowed_users: [] })).rejects.toThrow()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it.each([401, 403, 404, 422])('preserves sanitized API error status %i from the authenticated client', async (status) => {
    const request = vi.fn(async () => { throw new Error(`API ${status}`) }) as unknown as AuthenticatedRequest
    await expect(getDocumentPermissions(request, tenantId, documentId)).rejects.toThrow(`API ${status}`)
  })
})
