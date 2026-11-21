import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useMemo, useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { AuthContext } from '@/features/auth/auth-context'
import { ActiveTenantProvider } from './active-tenant-provider'
import { tenantQueryKey } from './query-keys'
import { useActiveTenant } from './active-tenant-context'
import type { TenantListItem } from './api'

afterEach(cleanup)

const tenantA: TenantListItem = {
  tenant: { id: '11111111-1111-4111-8111-111111111111', name: 'Tenant A', slug: 'tenant-a', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
  role: 'OWNER', joined_at: '2026-11-21T10:00:00Z',
}
const tenantB: TenantListItem = {
  tenant: { id: '22222222-2222-4222-8222-222222222222', name: 'Tenant B', slug: 'tenant-b', description: null, is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
  role: 'EMPLOYEE', joined_at: '2026-11-21T10:00:00Z',
}

function ProviderHarness({ onAbort }: { onAbort: () => void }) {
  const [authStatus, setAuthStatus] = useState<'authenticated' | 'unauthenticated'>('authenticated')
  const request = async <T,>(path: string): Promise<T> => {
    if (path === '/tenants') return [tenantA, tenantB] as T
    throw new Error('Unexpected test API path')
  }
  const auth = useMemo(() => ({
    status: authStatus, user: null, error: null, notice: null, login: async () => false, logout: () => undefined,
    clearError: () => undefined, authenticatedRequest: request,
  }), [authStatus])
  return <AuthContext.Provider value={auth}>
    <ActiveTenantProvider key={authStatus}>
      <TestWorkspace onLogout={() => setAuthStatus('unauthenticated')} onAbort={onAbort} />
    </ActiveTenantProvider>
  </AuthContext.Provider>
}

function TestWorkspace({ onLogout, onAbort }: { onLogout: () => void; onAbort: () => void }) {
  const { activeTenant, tenants, selectTenant, isSwitchingTenant } = useActiveTenant()
  const client = useQuery({
    queryKey: activeTenant ? tenantQueryKey(activeTenant.tenant.id, 'late') : ['tenant', 'none', 'late'],
    enabled: Boolean(activeTenant),
    queryFn: ({ signal }) => activeTenant?.tenant.id === tenantA.tenant.id
      ? new Promise<string>((resolve) => signal.addEventListener('abort', () => { onAbort(); resolve('late Tenant A response') }, { once: true }))
      : Promise.resolve('Tenant B response'),
  })
  return <div>
    <output data-testid="active-tenant">{activeTenant?.tenant.name ?? 'none'}</output>
    <output data-testid="switching">{String(isSwitchingTenant)}</output>
    <output data-testid="late-data">{client.data ?? 'loading'}</output>
    {tenants.map((item) => <button key={item.tenant.id} onClick={() => void selectTenant(item.tenant.id)}>Select {item.tenant.name}</button>)}
    <button onClick={onLogout}>Log out</button>
  </div>
}

function renderProvider(onAbort = () => undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<BrowserRouter><QueryClientProvider client={client}><ProviderHarness onAbort={onAbort} /></QueryClientProvider></BrowserRouter>)
  return { ...view, client }
}

describe('active tenant isolation', () => {
  it('cancels previous tenant work, removes its cache, and never displays a late result after switching', async () => {
    let aborted = false
    const { client } = renderProvider(() => { aborted = true })
    expect(await screen.findByRole('button', { name: 'Select Tenant A' })).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Select Tenant A' })) })
    expect(await screen.findByTestId('active-tenant')).toHaveTextContent('Tenant A')
    expect(await screen.findByTestId('late-data')).toHaveTextContent('loading')
    client.setQueryData(tenantQueryKey(tenantA.tenant.id, 'secret'), 'Tenant A cached data')

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Select Tenant B' })) })

    expect(await screen.findByTestId('active-tenant')).toHaveTextContent('Tenant B')
    expect(await screen.findByTestId('late-data')).toHaveTextContent('Tenant B response')
    expect(aborted).toBe(true)
    expect(client.getQueryData(tenantQueryKey(tenantA.tenant.id, 'secret'))).toBeUndefined()
    expect(client.getQueryData(tenantQueryKey(tenantA.tenant.id, 'late'))).toBeUndefined()
  })

  it('clears selected tenant state and tenant cache on logout without persisting it', async () => {
    const { client } = renderProvider()
    await screen.findByRole('button', { name: 'Select Tenant A' })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Select Tenant A' })) })
    expect(await screen.findByTestId('active-tenant')).toHaveTextContent('Tenant A')
    client.setQueryData(tenantQueryKey(tenantA.tenant.id, 'private'), 'private data')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log out' })) })

    expect(screen.getByTestId('active-tenant')).toHaveTextContent('none')
    expect(client.getQueryData(tenantQueryKey(tenantA.tenant.id, 'private'))).toBeUndefined()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
