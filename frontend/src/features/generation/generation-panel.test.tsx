import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
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
import { GenerationPanel } from './generation-panel'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const tenantId = '11111111-1111-4111-8111-111111111111'
const documentA = '22222222-2222-4222-8222-222222222222'
const documentB = '33333333-3333-4333-8333-333333333333'
const chunkA = '44444444-4444-4444-8444-444444444444'
const chunkB = '55555555-5555-4555-8555-555555555555'
const tenant = { id: tenantId, name: 'Tenant Answers', slug: 'tenant-answers', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }
const citations = [
  { document_id: documentA, chunk_id: chunkA, page_numbers: [2, 3], relevance_score: 0.91 },
  { document_id: documentB, chunk_id: chunkB, page_numbers: [7], relevance_score: 0.82 },
]

function renderPanel(role: TenantRole, request: AuthContextValue['authenticatedRequest']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const auth: AuthContextValue = { status: 'authenticated', user: { id: '66666666-6666-4666-8666-666666666666', email: 'member@example.test', full_name: 'Member', is_active: true, is_superuser: false, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }, error: null, notice: null, login: async () => false, logout: () => undefined, clearError: () => undefined, authenticatedRequest: request }
  const tenantContext: ActiveTenantContextValue = { tenants: [{ tenant, role, joined_at: tenant.created_at }], activeTenant: { tenant, role, joined_at: tenant.created_at }, isTenantsLoading: false, tenantsError: false, unavailableTenantNotice: false, isSwitchingTenant: false, refreshTenants: async () => [], selectTenant: async () => undefined, clearUnavailableTenant: () => undefined }
  const view = render(<MemoryRouter><QueryClientProvider client={queryClient}><AuthContext.Provider value={auth}><ActiveTenantContext.Provider value={tenantContext}><GenerationPanel tenantId={tenantId} /></ActiveTenantContext.Provider></AuthContext.Provider></QueryClientProvider></MemoryRouter>)
  return { ...view, queryClient }
}

describe('grounded answer panel', () => {
  it.each(['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE'] as const)('%s can ask questions and view ordered backend citations', async (role) => {
    const response = { query: 'leave policy', answer: 'Annual leave is available [S1]. Other details are on [S2].', citations, result_count: 2, grounded: true }
    const request = vi.fn(async () => response) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    const { queryClient } = renderPanel(role, request)
    await userEvent.type(screen.getByLabelText('Your question'), 'leave policy')
    await userEvent.click(screen.getByRole('button', { name: 'Generate answer' }))
    expect(await screen.findByRole('region', { name: 'Generated answer' })).toBeInTheDocument()
    expect(screen.getByText('Annual leave is available [S1]. Other details are on [S2].')).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { name: /\[S[12]\]/ })).toHaveLength(2)
    const sources = screen.getByRole('region', { name: 'Answer citations' })
    const sourceItems = within(sources).getAllByRole('listitem')
    expect(sourceItems[0]).toHaveTextContent('[S1]')
    expect(sourceItems[0]).toHaveTextContent(documentA.slice(0, 8))
    expect(sourceItems[0]).toHaveTextContent('2, 3')
    expect(sourceItems[0]).toHaveTextContent('0.910')
    expect(sourceItems[1]).toHaveTextContent('[S2]')
    expect(sourceItems[1]).toHaveTextContent(documentB.slice(0, 8))
    expect(sourceItems[1]).toHaveTextContent('7')
    expect(screen.getByText('Grounded')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/generation/answer`, expect.objectContaining({ method: 'POST', json: { query: 'leave policy', top_k: 5 }, signal: expect.any(AbortSignal) }))
    expect(request.mock.calls[0]?.[1]?.json).not.toHaveProperty('role')
    expect(request.mock.calls[0]?.[1]?.json).not.toHaveProperty('user_id')
    expect(queryClient.getQueryCache().findAll({ queryKey: tenantQueryKey(tenantId, 'generation', 'answer') })).toHaveLength(1)
  })

  it('renders model answer as escaped text and hides citations when grounding is false', async () => {
    const response = { query: 'q', answer: '<img src=x onerror=alert(1)> Insufficient.', citations, result_count: 0, grounded: false }
    const request = vi.fn(async () => response) as unknown as AuthContextValue['authenticatedRequest']
    renderPanel('EMPLOYEE', request)
    await userEvent.type(screen.getByLabelText('Your question'), 'q')
    await userEvent.click(screen.getByRole('button', { name: 'Generate answer' }))
    expect(await screen.findByText(/available authorized documents did not provide sufficient information/i)).toBeInTheDocument()
    expect(screen.getByText('<img src=x onerror=alert(1)> Insufficient.')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Answer citations' })).not.toBeInTheDocument()
    expect(screen.getByText('Insufficient information')).toBeInTheDocument()
  })

  it('does not auto-repeat an ambiguous failed generation request and retries only on user action', async () => {
    let calls = 0
    const request = vi.fn(async () => {
      calls += 1
      if (calls === 1) throw new ApiError('sensitive provider error', 'http', 503)
      return { query: 'question', answer: 'No supporting context.', citations: [], result_count: 0, grounded: false }
    }) as unknown as AuthContextValue['authenticatedRequest']
    renderPanel('MANAGER', request)
    await userEvent.type(screen.getByLabelText('Your question'), 'question')
    await userEvent.click(screen.getByRole('button', { name: 'Generate answer' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Answer generation is temporarily unavailable.')
    expect(screen.queryByText('sensitive provider error')).not.toBeInTheDocument()
    expect(calls).toBe(1)
    await userEvent.click(screen.getByRole('button', { name: 'Retry answer' }))
    expect(await screen.findByText('Insufficient information')).toBeInTheDocument()
    expect(calls).toBe(2)
  })
})
