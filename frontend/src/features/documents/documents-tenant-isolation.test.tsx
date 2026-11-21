import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useMemo } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { AuthContext } from '@/features/auth/auth-context'
import type { AuthContextValue } from '@/features/auth/auth-context'
import { DocumentsPage } from '@/features/documents/documents-page'
import type { DocumentPage, DocumentRecord } from '@/features/documents/api'
import { ActiveTenantProvider } from '@/features/tenants/active-tenant-provider'
import { useActiveTenant } from '@/features/tenants/active-tenant-context'
import type { TenantListItem } from '@/features/tenants/api'
import { tenantQueryKey } from '@/features/tenants/query-keys'

afterEach(cleanup)

const tenantA: TenantListItem = {
  tenant: { id: '11111111-1111-4111-8111-111111111111', name: 'Tenant A', slug: 'tenant-a', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
  role: 'OWNER', joined_at: '2026-11-21T10:00:00Z',
}
const tenantB: TenantListItem = {
  tenant: { id: '22222222-2222-4222-8222-222222222222', name: 'Tenant B', slug: 'tenant-b', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
  role: 'EMPLOYEE', joined_at: '2026-11-21T10:00:00Z',
}

function tenantDocument(tenant: TenantListItem, id: string, name: string): DocumentRecord {
  return {
    id, tenant_id: tenant.tenant.id, uploaded_by: '33333333-3333-4333-8333-333333333333',
    filename: `${id}.pdf`, original_filename: name, file_hash: 'f'.repeat(64), file_size: 512,
    content_type: 'application/pdf', page_count: 1, chunk_count: 1, status: 'COMPLETED',
    classification: 1, is_public: false, allowed_roles: [], allowed_users: [],
    created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z', processing_completed_at: '2026-11-21T10:01:00Z',
  }
}

const tenantADocument = tenantDocument(tenantA, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Tenant A confidential policy.pdf')
const tenantBDocument = tenantDocument(tenantB, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Tenant B handbook.pdf')
const pageFor = (items: DocumentRecord[]): DocumentPage => ({ items, total: items.length, limit: 50, offset: 0 })
const tenantListPath = '/tenants'

function TenantControls() {
  const { tenants, selectTenant, activeTenant } = useActiveTenant()
  return <div>
    <output aria-label="Active tenant">{activeTenant?.tenant.name ?? 'No active tenant'}</output>
    {tenants.map((item) => <button key={item.tenant.id} onClick={() => void selectTenant(item.tenant.id)}>Switch to {item.tenant.name}</button>)}
  </div>
}

function renderDocumentWorkspace(listHandler: (tenantId: string, signal: AbortSignal) => Promise<DocumentPage>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const request: AuthContextValue['authenticatedRequest'] = async <T,>(path: string, options: Parameters<AuthContextValue['authenticatedRequest']>[1]) => {
    if (path === tenantListPath) return [tenantA, tenantB] as T
    const match = path.match(/^\/tenants\/([^/]+)\/documents\?limit=50&offset=0$/)
    if (match && options?.signal) return await listHandler(match[1]!, options.signal) as T
    throw new Error(`Unexpected test request: ${path}`)
  }
  const auth: AuthContextValue = {
    status: 'authenticated',
    user: { id: '55555555-5555-4555-8555-555555555555', email: 'member@example.test', full_name: 'Tenant Member', is_active: true, is_superuser: false, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
    error: null, notice: null, login: async () => false, logout: () => undefined, clearError: () => undefined,
    authenticatedRequest: request,
  }
  function Harness() {
    const value = useMemo(() => auth, [])
    return <AuthContext.Provider value={value}>
      <ActiveTenantProvider>
        <TenantControls />
        <DocumentsPage />
      </ActiveTenantProvider>
    </AuthContext.Provider>
  }
  const view = render(<BrowserRouter><QueryClientProvider client={queryClient}><Harness /></QueryClientProvider></BrowserRouter>)
  return { ...view, queryClient }
}

describe('document tenant isolation through the active tenant provider', () => {
  it('removes Tenant A document cache and renders only Tenant B documents after switching', async () => {
    const { queryClient } = renderDocumentWorkspace(async (tenantId) => tenantId === tenantA.tenant.id
      ? pageFor([tenantADocument])
      : pageFor([tenantBDocument]))
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Switch to Tenant A' }))
    expect(await screen.findByText('Tenant A confidential policy.pdf')).toBeInTheDocument()
    expect(queryClient.getQueryData(tenantQueryKey(tenantA.tenant.id, 'documents', 'list', { limit: 50, offset: 0 }))).toEqual(pageFor([tenantADocument]))

    await user.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(await screen.findByRole('heading', { name: 'Documents' })).toBeInTheDocument()
    expect(await screen.findByText('Tenant B handbook.pdf')).toBeInTheDocument()
    expect(screen.queryByText('Tenant A confidential policy.pdf')).not.toBeInTheDocument()
    expect(queryClient.getQueryData(tenantQueryKey(tenantA.tenant.id, 'documents', 'list', { limit: 50, offset: 0 }))).toBeUndefined()
    expect(queryClient.getQueryData(tenantQueryKey(tenantB.tenant.id, 'documents', 'list', { limit: 50, offset: 0 }))).toEqual(pageFor([tenantBDocument]))
  })

  it('aborts an unresolved Tenant A list request and loads Tenant B normally', async () => {
    let tenantASignal: AbortSignal | undefined
    let resolveTenantA!: (page: DocumentPage) => void
    const { queryClient } = renderDocumentWorkspace((tenantId, signal) => {
      if (tenantId === tenantA.tenant.id) {
        tenantASignal = signal
        return new Promise<DocumentPage>((resolve) => { resolveTenantA = resolve })
      }
      return Promise.resolve(pageFor([tenantBDocument]))
    })
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Switch to Tenant A' }))
    expect(await screen.findByText('Loading documents')).toBeInTheDocument()
    expect(tenantASignal?.aborted).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(tenantASignal?.aborted).toBe(true)
    expect(await screen.findByText('Tenant B handbook.pdf')).toBeInTheDocument()
    expect(queryClient.getQueryData(tenantQueryKey(tenantB.tenant.id, 'documents', 'list', { limit: 50, offset: 0 }))).toEqual(pageFor([tenantBDocument]))

    // Resolve the cancelled transport to ensure the query client discards its late result.
    await act(async () => { resolveTenantA(pageFor([tenantADocument])) })
    expect(screen.queryByText('Tenant A confidential policy.pdf')).not.toBeInTheDocument()
    expect(screen.getByText('Tenant B handbook.pdf')).toBeInTheDocument()
    expect(queryClient.getQueryData(tenantQueryKey(tenantA.tenant.id, 'documents', 'list', { limit: 50, offset: 0 }))).toBeUndefined()
  })

  it('never displays a late Tenant A response after Tenant B has become active', async () => {
    let resolveTenantA!: (page: DocumentPage) => void
    let tenantAStarted!: () => void
    const started = new Promise<void>((resolve) => { tenantAStarted = resolve })
    renderDocumentWorkspace((tenantId) => tenantId === tenantA.tenant.id
      ? new Promise<DocumentPage>((resolve) => { resolveTenantA = (page) => resolve(page); tenantAStarted() })
      : Promise.resolve(pageFor([tenantBDocument])))
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Switch to Tenant A' }))
    await started
    expect(screen.getByText('Loading documents')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(await screen.findByText('Tenant B handbook.pdf')).toBeInTheDocument()

    await act(async () => { resolveTenantA(pageFor([tenantADocument])) })
    expect(screen.getByLabelText('Active tenant')).toHaveTextContent('Tenant B')
    expect(screen.getByText('Tenant B handbook.pdf')).toBeInTheDocument()
    expect(screen.queryByText('Tenant A confidential policy.pdf')).not.toBeInTheDocument()
  })
})
