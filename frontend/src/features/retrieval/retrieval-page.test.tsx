import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '@/features/auth/auth-context'
import type { AuthContextValue } from '@/features/auth/auth-context'
import { ActiveTenantContext } from '@/features/tenants/active-tenant-context'
import type { ActiveTenantContextValue } from '@/features/tenants/active-tenant-context'
import type { TenantRole } from '@/features/tenants/api'
import { ApiError } from '@/lib/api/api-error'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { KnowledgePage } from './retrieval-page'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const tenantId = '11111111-1111-4111-8111-111111111111'
const documentId = '22222222-2222-4222-8222-222222222222'
const chunkId = '33333333-3333-4333-8333-333333333333'
const tenant = { id: tenantId, name: 'Tenant Knowledge', slug: 'tenant-knowledge', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }
const result = { chunk_id: chunkId, document_id: documentId, chunk_index: 4, page_numbers: [3, 4], text: '<script>alert("x")</script> authorized excerpt', similarity_score: 0.87654 }

function renderPage(role: TenantRole, request: AuthContextValue['authenticatedRequest']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const auth: AuthContextValue = { status: 'authenticated', user: { id: '44444444-4444-4444-8444-444444444444', email: 'member@example.test', full_name: 'Member', is_active: true, is_superuser: false, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }, error: null, notice: null, login: async () => false, logout: () => undefined, clearError: () => undefined, authenticatedRequest: request }
  const tenantContext: ActiveTenantContextValue = { tenants: [{ tenant, role, joined_at: tenant.created_at }], activeTenant: { tenant, role, joined_at: tenant.created_at }, isTenantsLoading: false, tenantsError: false, unavailableTenantNotice: false, isSwitchingTenant: false, refreshTenants: async () => [], selectTenant: async () => undefined, clearUnavailableTenant: () => undefined }
  const view = render(<MemoryRouter><QueryClientProvider client={queryClient}><AuthContext.Provider value={auth}><ActiveTenantContext.Provider value={tenantContext}><KnowledgePage /></ActiveTenantContext.Provider></AuthContext.Provider></QueryClientProvider></MemoryRouter>)
  return { ...view, queryClient }
}

describe('knowledge retrieval workspace', () => {
  it.each(['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE'] as const)('%s can search authorized document excerpts', async (role) => {
    const request = vi.fn(async () => ({ query: 'leave policy', tenant_id: tenantId, results: [result], result_count: 1 })) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    const { queryClient } = renderPage(role, request)
    await userEvent.type(screen.getByLabelText('Search query'), 'leave policy')
    fireEvent.change(screen.getByLabelText('Results'), { target: { value: '3' } })
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText(/authorized excerpt/)).toBeInTheDocument()
    expect(screen.getByText('Pages')).toBeInTheDocument()
    expect(screen.getByText('3, 4')).toBeInTheDocument()
    expect(screen.getByText('Similarity 0.877')).toBeInTheDocument()
    expect(screen.getByText('1 result returned.')).toBeInTheDocument()
    expect(screen.queryByRole('script')).not.toBeInTheDocument()
    expect(screen.getByText('<script>alert("x")</script> authorized excerpt')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/retrieval/search`, expect.objectContaining({ method: 'POST', json: { query: 'leave policy', top_k: 3 }, signal: expect.any(AbortSignal) }))
    expect(request.mock.calls[0]?.[1]?.json).not.toHaveProperty('role')
    expect(request.mock.calls[0]?.[1]?.json).not.toHaveProperty('user_id')
    expect(queryClient.getQueryCache().findAll({ queryKey: tenantQueryKey(tenantId, 'retrieval', 'search') })).toHaveLength(1)
  })

  it('validates query and result count before sending and handles an empty result set', async () => {
    const request = vi.fn(async () => ({ query: 'query', tenant_id: tenantId, results: [], result_count: 0 })) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderPage('EMPLOYEE', request)
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('Enter a search query.')).toBeInTheDocument()
    expect(request).not.toHaveBeenCalled()
    await userEvent.type(screen.getByLabelText('Search query'), 'query')
    await userEvent.clear(screen.getByLabelText('Results'))
    await userEvent.type(screen.getByLabelText('Results'), '21')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('Choose no more than 20 results.')).toBeInTheDocument()
    expect(request).not.toHaveBeenCalled()
    await userEvent.clear(screen.getByLabelText('Results'))
    await userEvent.type(screen.getByLabelText('Results'), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('No authorized excerpts matched this query.')).toBeInTheDocument()
  })

  it('shows loading and safe errors with deliberate retry, replacing prior results for a new query', async () => {
    let calls = 0
    let release!: (value: unknown) => void
    const request = vi.fn(async (_path: string, options?: { json?: { query: string } }) => {
      calls += 1
      if (calls === 1) return { query: 'first', tenant_id: tenantId, results: [result], result_count: 1 }
      if (calls === 2) throw new ApiError('private Qdrant exception', 'http', 503)
      if (calls === 3) return new Promise((resolve) => { release = resolve })
      return { query: options?.json?.query ?? 'retry', tenant_id: tenantId, results: [result], result_count: 1 }
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderPage('HR', request)
    await userEvent.type(screen.getByLabelText('Search query'), 'first')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText(/authorized excerpt/)).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('Search query'))
    await userEvent.type(screen.getByLabelText('Search query'), 'second')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Retrieval is temporarily unavailable.')
    expect(screen.queryByText('private Qdrant exception')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry search' }))
    expect(await screen.findByText('Searching authorized documents')).toBeInTheDocument()
    release({ query: 'second', tenant_id: tenantId, results: [result], result_count: 1 })
    expect(await screen.findByText('1 result returned.')).toBeInTheDocument()
    expect(calls).toBe(3)
  })
})
