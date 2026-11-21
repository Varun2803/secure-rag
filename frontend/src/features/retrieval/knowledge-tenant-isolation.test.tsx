import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useMemo } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { AuthContext } from '@/features/auth/auth-context'
import type { AuthContextValue } from '@/features/auth/auth-context'
import { ActiveTenantProvider } from '@/features/tenants/active-tenant-provider'
import { useActiveTenant } from '@/features/tenants/active-tenant-context'
import type { TenantListItem } from '@/features/tenants/api'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { KnowledgePage } from './retrieval-page'

afterEach(cleanup)

const tenantA: TenantListItem = { tenant: { id: '11111111-1111-4111-8111-111111111111', name: 'Tenant A', slug: 'tenant-a', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }, role: 'OWNER', joined_at: '2026-11-21T10:00:00Z' }
const tenantB: TenantListItem = { tenant: { id: '22222222-2222-4222-8222-222222222222', name: 'Tenant B', slug: 'tenant-b', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }, role: 'EMPLOYEE', joined_at: '2026-11-21T10:00:00Z' }
const tenantListPath = '/tenants'

function TenantControls() {
  const { tenants, activeTenant, selectTenant } = useActiveTenant()
  return <div><output aria-label="Active tenant">{activeTenant?.tenant.name ?? 'No active tenant'}</output>{tenants.map((item) => <button key={item.tenant.id} onClick={() => void selectTenant(item.tenant.id)}>Switch to {item.tenant.name}</button>)}</div>
}

type RequestHandler = (path: string, options: Parameters<AuthContextValue['authenticatedRequest']>[1]) => Promise<unknown>

function renderKnowledgeApp(handler: RequestHandler) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const request: AuthContextValue['authenticatedRequest'] = async <T,>(path: string, options: Parameters<AuthContextValue['authenticatedRequest']>[1]) => {
    if (path === tenantListPath) return [tenantA, tenantB] as T
    return await handler(path, options) as T
  }
  const auth: AuthContextValue = { status: 'authenticated', user: { id: '33333333-3333-4333-8333-333333333333', email: 'member@example.test', full_name: 'Tenant Member', is_active: true, is_superuser: false, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' }, error: null, notice: null, login: async () => false, logout: () => undefined, clearError: () => undefined, authenticatedRequest: request }
  function Harness() {
    const value = useMemo(() => auth, [])
    return <AuthContext.Provider value={value}><ActiveTenantProvider><TenantControls /><KnowledgePage /></ActiveTenantProvider></AuthContext.Provider>
  }
  const view = render(<BrowserRouter><QueryClientProvider client={queryClient}><Harness /></QueryClientProvider></BrowserRouter>)
  return { ...view, queryClient }
}

async function selectTenant(name: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Switch to ${name}` }))
  expect(await screen.findByRole('heading', { name: 'Knowledge Search' })).toBeInTheDocument()
}

function retrievalResponse(tenantId: string, text: string) {
  return { query: 'policy', tenant_id: tenantId, results: [{ chunk_id: tenantId === tenantA.tenant.id ? 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' : 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', document_id: tenantId, chunk_index: 0, page_numbers: [1], text, similarity_score: 0.9 }], result_count: 1 }
}

function generationResponse(answer: string, documentId: string) {
  return { query: 'policy', answer, citations: [{ document_id: documentId, chunk_id: documentId === tenantA.tenant.id ? 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' : 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', page_numbers: [1], relevance_score: 0.9 }], result_count: 1, grounded: true }
}

describe('retrieval and generation tenant isolation through ActiveTenantProvider', () => {
  it('removes Tenant A retrieval and answer caches and displays only Tenant B responses', async () => {
    const { queryClient } = renderKnowledgeApp(async (path) => {
      const tenantId = path.split('/')[2]!
      if (path.endsWith('/retrieval/search')) return retrievalResponse(tenantId, tenantId === tenantA.tenant.id ? 'Tenant A authorized excerpt' : 'Tenant B authorized excerpt')
      if (path.endsWith('/generation/answer')) return generationResponse(tenantId === tenantA.tenant.id ? 'Tenant A grounded answer [S1]' : 'Tenant B grounded answer [S1]', tenantId)
      throw new Error(`Unsupported API path: ${path}`)
    })
    await selectTenant('Tenant A')
    await userEvent.type(screen.getByLabelText('Search query'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('Tenant A authorized excerpt')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Your question'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Generate answer' }))
    expect(await screen.findByText('Tenant A grounded answer [S1]')).toBeInTheDocument()
    expect(queryClient.getQueryCache().findAll({ queryKey: tenantQueryKey(tenantA.tenant.id, 'retrieval') }).filter((query) => query.queryKey.at(-1) !== 'idle')).toHaveLength(1)
    expect(queryClient.getQueryCache().findAll({ queryKey: tenantQueryKey(tenantA.tenant.id, 'generation') }).filter((query) => query.queryKey.at(-1) !== 'idle')).toHaveLength(1)

    await userEvent.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(await screen.findByLabelText('Search query')).toHaveValue('')
    expect(screen.getByLabelText('Your question')).toHaveValue('')
    expect(queryClient.getQueryCache().findAll({ queryKey: tenantQueryKey(tenantA.tenant.id, 'retrieval') })).toHaveLength(0)
    expect(queryClient.getQueryCache().findAll({ queryKey: tenantQueryKey(tenantA.tenant.id, 'generation') })).toHaveLength(0)
    await userEvent.type(screen.getByLabelText('Search query'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('Tenant B authorized excerpt')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Your question'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Generate answer' }))
    expect(await screen.findByText('Tenant B grounded answer [S1]')).toBeInTheDocument()
    expect(screen.queryByText('Tenant A authorized excerpt')).not.toBeInTheDocument()
    expect(screen.queryByText('Tenant A grounded answer [S1]')).not.toBeInTheDocument()
  })

  it('cancels in-flight Tenant A retrieval and generation and discards their late responses', async () => {
    let searchSignalA: AbortSignal | undefined
    let generationSignalA: AbortSignal | undefined
    let resolveSearchA!: (value: unknown) => void
    let resolveGenerationA!: (value: unknown) => void
    const app = renderKnowledgeApp((path, options) => {
      const tenantId = path.split('/')[2]!
      if (tenantId === tenantA.tenant.id && path.endsWith('/retrieval/search')) {
        searchSignalA = options?.signal ?? undefined
        return new Promise((resolve) => { resolveSearchA = resolve })
      }
      if (tenantId === tenantA.tenant.id && path.endsWith('/generation/answer')) {
        generationSignalA = options?.signal ?? undefined
        return new Promise((resolve) => { resolveGenerationA = resolve })
      }
      if (path.endsWith('/retrieval/search')) return Promise.resolve(retrievalResponse(tenantId, 'Tenant B search only'))
      if (path.endsWith('/generation/answer')) return Promise.resolve(generationResponse('Tenant B answer only [S1]', tenantId))
      return Promise.reject(new Error(`Unsupported API path: ${path}`))
    })
    await selectTenant('Tenant A')
    await userEvent.type(screen.getByLabelText('Search query'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await screen.findByText('Searching authorized documents')
    await userEvent.type(screen.getByLabelText('Your question'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Generate answer' }))
    await screen.findByText('Retrieving authorized context and generating an answer')

    await userEvent.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(searchSignalA?.aborted).toBe(true)
    expect(generationSignalA?.aborted).toBe(true)
    await screen.findByLabelText('Search query')
    await userEvent.type(screen.getByLabelText('Search query'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('Tenant B search only')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Your question'), 'policy')
    await userEvent.click(screen.getByRole('button', { name: 'Generate answer' }))
    expect(await screen.findByText('Tenant B answer only [S1]')).toBeInTheDocument()

    await act(async () => {
      resolveSearchA(retrievalResponse(tenantA.tenant.id, 'Late Tenant A secret excerpt'))
      resolveGenerationA(generationResponse('Late Tenant A secret answer [S1]', tenantA.tenant.id))
    })
    expect(screen.getByLabelText('Active tenant')).toHaveTextContent('Tenant B')
    expect(screen.queryByText('Late Tenant A secret excerpt')).not.toBeInTheDocument()
    expect(screen.queryByText('Late Tenant A secret answer [S1]')).not.toBeInTheDocument()
    expect(screen.getByText('Tenant B search only')).toBeInTheDocument()
    expect(screen.getByText('Tenant B answer only [S1]')).toBeInTheDocument()
    expect(app.queryClient.getQueryCache().findAll({ queryKey: tenantQueryKey(tenantA.tenant.id) })).toHaveLength(0)
  })
})
