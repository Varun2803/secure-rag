import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '@/features/auth/auth-context'
import type { AuthContextValue } from '@/features/auth/auth-context'
import { ActiveTenantContext } from '@/features/tenants/active-tenant-context'
import type { ActiveTenantContextValue } from '@/features/tenants/active-tenant-context'
import type { TenantRole } from '@/features/tenants/api'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { ApiError } from '@/lib/api/api-error'
import { DocumentsPage } from './documents-page'
import type { DocumentPage, DocumentRecord } from './api'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

const tenantId = '11111111-1111-4111-8111-111111111111'
const documentId = '22222222-2222-4222-8222-222222222222'
const tenant = { id: tenantId, name: 'Tenant A', slug: 'tenant-a', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }
const record: DocumentRecord = {
  id: documentId, tenant_id: tenantId, uploaded_by: '33333333-3333-4333-8333-333333333333',
  filename: 'internal-storage-name.pdf', original_filename: 'Annual Policy.pdf', file_hash: 'sensitive-hash-value',
  file_size: 2 * 1024 * 1024, content_type: 'application/pdf', page_count: 5, chunk_count: 8, status: 'COMPLETED',
  classification: 2, is_public: false, allowed_roles: [], allowed_users: ['44444444-4444-4444-8444-444444444444'],
  created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:01:00Z', processing_completed_at: '2026-11-21T10:01:00Z',
}
const emptyPage: DocumentPage = { items: [], total: 0, limit: 50, offset: 0 }

function renderPage(role: TenantRole, request: AuthContextValue['authenticatedRequest']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const auth: AuthContextValue = {
    status: 'authenticated', user: { id: '55555555-5555-4555-8555-555555555555', email: 'viewer@example.test', full_name: 'Workspace User', is_active: true, is_superuser: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
    error: null, notice: null, login: async () => false, logout: () => undefined, clearError: () => undefined, authenticatedRequest: request,
  }
  const activeContext: ActiveTenantContextValue = {
    tenants: [{ tenant, role, joined_at: tenant.created_at }], activeTenant: { tenant, role, joined_at: tenant.created_at },
    isTenantsLoading: false, tenantsError: false, unavailableTenantNotice: false, isSwitchingTenant: false,
    refreshTenants: async () => [], selectTenant: async () => undefined, clearUnavailableTenant: () => undefined,
  }
  const view = render(<MemoryRouter><QueryClientProvider client={queryClient}><AuthContext.Provider value={auth}><ActiveTenantContext.Provider value={activeContext}><DocumentsPage /></ActiveTenantContext.Provider></AuthContext.Provider></QueryClientProvider></MemoryRouter>)
  return { ...view, queryClient }
}

function listRequest(response: unknown = { ...emptyPage, items: [record], total: 1 }) {
  return vi.fn(async (path: string) => {
    if (path.includes('/documents?')) return response
    if (path.endsWith(`/documents/${documentId}`)) return record
    if (path.endsWith(`/documents/${documentId}/status`)) return { id: documentId, status: record.status, page_count: record.page_count, chunk_count: record.chunk_count, failure_detail: null }
    throw new Error(`Unexpected request: ${path}`)
  }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
}

describe('documents workspace', () => {
  it('lists safe document metadata, provides status details, and avoids rendering internal metadata', async () => {
    const request = listRequest()
    renderPage('EMPLOYEE', request)

    expect(await screen.findByText('Annual Policy.pdf')).toBeInTheDocument()
    expect(screen.getByText('COMPLETED')).toBeInTheDocument()
    expect(screen.getByText('5 pages')).toBeInTheDocument()
    expect(screen.getByText('8 chunks')).toBeInTheDocument()
    expect(screen.getByText('Confidential')).toBeInTheDocument()
    expect(screen.queryByText('internal-storage-name.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('sensitive-hash-value')).not.toBeInTheDocument()
    expect(screen.queryByText('44444444-4444-4444-8444-444444444444')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'View details' }))
    expect(await screen.findByRole('region', { name: 'Document details' })).toBeInTheDocument()
    expect(await screen.findByText('Refresh status')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}/status`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    const statusCalls = request.mock.calls.filter(([path]) => path === `/tenants/${tenantId}/documents/${documentId}/status`)
    await userEvent.click(screen.getByRole('button', { name: 'Refresh status' }))
    await waitForStatusRefresh(request, statusCalls.length + 1)
  })

  it('shows an empty state and total count when no documents exist', async () => {
    renderPage('EMPLOYEE', listRequest(emptyPage))
    expect(await screen.findByText(/No documents have been uploaded/)).toBeInTheDocument()
    expect(screen.getByText('0 documents total')).toBeInTheDocument()
  })

  it('shows loading state and recovers from a list error using Retry', async () => {
    let shouldFail = true
    let rejectList!: (reason: Error) => void
    const request = vi.fn(async (path: string) => {
      if (!path.includes('/documents?')) throw new Error(`Unexpected request: ${path}`)
      if (shouldFail) return new Promise<unknown>((_resolve, reject) => { rejectList = reject })
      return { ...emptyPage, items: [record], total: 1 }
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderPage('EMPLOYEE', request)
    expect(await screen.findByText('Loading documents')).toBeInTheDocument()
    await act(async () => { rejectList(new ApiError('internal failure', 'http', 503)) })
    expect(await screen.findByText('Documents unavailable')).toBeInTheDocument()
    shouldFail = false
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Annual Policy.pdf')).toBeInTheDocument()
  })

  it('paginates using the backend limit, total and offset', async () => {
    const second = { ...record, id: '66666666-6666-4666-8666-666666666666', original_filename: 'Page two.pdf' }
    const request = vi.fn(async (path: string) => path.endsWith('offset=0')
      ? { items: [record], total: 51, limit: 50, offset: 0 }
      : { items: [second], total: 51, limit: 50, offset: 50 }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderPage('EMPLOYEE', request)
    expect(await screen.findByText('Page 1 of 2')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Page two.pdf')).toBeInTheDocument()
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents?limit=50&offset=50`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it.each(['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE'] as const)('%s upload controls match the tenant membership role', async (role) => {
    const request = listRequest(emptyPage)
    renderPage(role, request)
    await screen.findByText('Tenant documents')
    if (['OWNER', 'ADMIN', 'HR'].includes(role)) expect(screen.getByLabelText('PDF file')).toBeInTheDocument()
    else expect(screen.queryByLabelText('PDF file')).not.toBeInTheDocument()
  })

  it('rejects non-PDF extensions and files over 20 MiB before sending an upload', async () => {
    const request = listRequest(emptyPage)
    renderPage('HR', request)
    const input = await screen.findByLabelText('PDF file')
    fireEvent.change(input, { target: { files: [new File(['text'], 'notes.txt', { type: 'application/pdf' })] } })
    await userEvent.click(screen.getByRole('button', { name: 'Upload PDF' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('.pdf extension')
    expect(request).not.toHaveBeenCalledWith(expect.stringContaining('/upload'), expect.anything())

    const oversized = new File([new Uint8Array(20 * 1024 * 1024 + 1)], 'oversized.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [oversized] } })
    await userEvent.click(screen.getByRole('button', { name: 'Upload PDF' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('20 MiB')
    expect(request).not.toHaveBeenCalledWith(expect.stringContaining('/upload'), expect.anything())
  })

  it('uploads a PDF synchronously and refreshes only the active tenant list', async () => {
    let uploaded = false
    const request = vi.fn(async (path: string, options?: { method?: string; formData?: FormData }) => {
      if (path.includes('/documents?')) return uploaded ? { ...emptyPage, items: [record], total: 1 } : emptyPage
      if (path.endsWith('/documents/upload')) {
        expect(options?.method).toBe('POST')
        expect(options?.formData?.get('file')).toBeInstanceOf(File)
        uploaded = true
        return record
      }
      throw new Error(`Unexpected request: ${path}`)
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    const { queryClient } = renderPage('HR', request)
    await screen.findByText(/No documents have been uploaded/)
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents?limit=50&offset=0`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    const input = screen.getByLabelText('PDF file')
    await userEvent.upload(input, new File(['%PDF-1.7'], 'new-policy.pdf', { type: 'application/pdf' }))
    await userEvent.click(screen.getByRole('button', { name: 'Upload PDF' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Annual Policy.pdf was uploaded and processed.')
    expect(await screen.findByText('Annual Policy.pdf')).toBeInTheDocument()
    expect(input).toHaveValue('')
    expect(queryClient.getQueryData(tenantQueryKey(tenantId, 'documents', 'list', { limit: 50, offset: 0 }))).toBeDefined()
  })

  it.each([[401, 'sign-in'], [403, 'role does not allow'], [404, 'unavailable'], [409, 'already exists'], [413, '20 MiB'], [415, 'Only PDF'], [422, 'invalid'], [503, 'temporarily unavailable']] as const)('shows a sanitized upload error for HTTP %i', async (status, message) => {
    const request = vi.fn(async (path: string) => {
      if (path.includes('/documents?')) return emptyPage
      if (path.endsWith('/documents/upload')) throw new ApiError('sensitive server detail', 'http', status)
      throw new Error(`Unexpected request: ${path}`)
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderPage('OWNER', request)
    await screen.findByText(/No documents have been uploaded/)
    await userEvent.upload(screen.getByLabelText('PDF file'), new File(['pdf'], 'duplicate.pdf', { type: 'application/pdf' }))
    await userEvent.click(screen.getByRole('button', { name: 'Upload PDF' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(message)
    expect(alert).not.toHaveTextContent('sensitive server detail')
  })

  it('disables duplicate submission while upload is pending', async () => {
    let resolveUpload!: (value: DocumentRecord) => void
    const request = vi.fn(async (path: string) => {
      if (path.includes('/documents?')) return emptyPage
      if (path.endsWith('/documents/upload')) return new Promise<DocumentRecord>((resolve) => { resolveUpload = resolve })
      throw new Error(`Unexpected request: ${path}`)
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderPage('ADMIN', request)
    await screen.findByText(/No documents have been uploaded/)
    await userEvent.upload(screen.getByLabelText('PDF file'), new File(['pdf'], 'new.pdf', { type: 'application/pdf' }))
    const uploadButton = screen.getByRole('button', { name: 'Upload PDF' })
    await userEvent.click(uploadButton)
    expect(await screen.findByRole('button', { name: 'Uploading and processing…' })).toBeDisabled()
    expect(request).toHaveBeenCalledTimes(2)
    resolveUpload(record)
    await screen.findByRole('status')
  })
})

async function waitForStatusRefresh(request: ReturnType<typeof vi.fn>, expectedCalls: number) {
  await vi.waitFor(() => {
    expect(request.mock.calls.filter(([path]) => path === `/tenants/${tenantId}/documents/${documentId}/status`)).toHaveLength(expectedCalls)
  })
}
