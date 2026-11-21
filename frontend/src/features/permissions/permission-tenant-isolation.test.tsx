import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useMemo } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { AuthContext } from '@/features/auth/auth-context'
import type { AuthContextValue } from '@/features/auth/auth-context'
import { DocumentsPage } from '@/features/documents/documents-page'
import { ApiError } from '@/lib/api/api-error'
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
  role: 'ADMIN', joined_at: '2026-11-21T10:00:00Z',
}
const documentA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const documentB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const activeUser = '33333333-3333-4333-8333-333333333333'

function permissionResponse(tenantId: string, documentId: string, classification: string, role: string) {
  return { tenant_id: tenantId, document_id: documentId, classification, is_public: false, allowed_roles: [role], allowed_users: [], permission_updated_at: '2026-11-21T10:00:00Z', synchronization_status: 'COMPLETED' }
}

const documents = [
  { id: documentA, tenant_id: tenantA.tenant.id, uploaded_by: activeUser, filename: 'a.pdf', original_filename: 'Tenant policy.pdf', file_hash: 'a'.repeat(64), file_size: 100, content_type: 'application/pdf', page_count: 1, chunk_count: 1, status: 'COMPLETED', classification: 1, is_public: false, allowed_roles: [], allowed_users: [], created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z', processing_completed_at: '2026-11-21T10:00:00Z' },
  { id: documentB, tenant_id: tenantB.tenant.id, uploaded_by: activeUser, filename: 'b.pdf', original_filename: 'Tenant policy.pdf', file_hash: 'b'.repeat(64), file_size: 100, content_type: 'application/pdf', page_count: 1, chunk_count: 1, status: 'COMPLETED', classification: 1, is_public: false, allowed_roles: [], allowed_users: [], created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z', processing_completed_at: '2026-11-21T10:00:00Z' },
]
const membership = (tenantId: string) => [{ id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', tenant_id: tenantId, user_id: activeUser, role: 'OWNER', is_active: true, joined_at: '2026-11-21T10:00:00Z', created_at: '2026-11-21T10:00:00Z', user: { id: activeUser, email: 'owner@example.test', full_name: 'Tenant Owner', is_active: true } }]

function TenantControls() {
  const { tenants, activeTenant, selectTenant } = useActiveTenant()
  return <div><output aria-label="Active tenant">{activeTenant?.tenant.name ?? 'No active tenant'}</output>{tenants.map((item) => <button key={item.tenant.id} onClick={() => void selectTenant(item.tenant.id)}>Switch to {item.tenant.name}</button>)}</div>
}

function renderWorkspace(permissionHandler: (tenantId: string, documentId: string, signal: AbortSignal | undefined, method: string | undefined) => Promise<unknown>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const request: AuthContextValue['authenticatedRequest'] = async <T,>(path: string, options: Parameters<AuthContextValue['authenticatedRequest']>[1]) => {
    if (path === '/tenants') return [tenantA, tenantB] as T
    const listMatch = path.match(/^\/tenants\/([^/]+)\/documents\?limit=50&offset=0$/)
    if (listMatch) {
      const rows = documents.filter((item) => item.tenant_id === listMatch[1])
      return { items: rows, total: rows.length, limit: 50, offset: 0 } as T
    }
    const permissionMatch = path.match(/^\/tenants\/([^/]+)\/documents\/([^/]+)\/permissions$/)
    if (permissionMatch) return await permissionHandler(permissionMatch[1]!, permissionMatch[2]!, options?.signal ?? undefined, options?.method) as T
    const membersMatch = path.match(/^\/tenants\/([^/]+)\/memberships$/)
    if (membersMatch) return membership(membersMatch[1]!) as T
    throw new Error(`Unsupported endpoint requested: ${path}`)
  }
  const auth: AuthContextValue = {
    status: 'authenticated', user: { id: activeUser, email: 'owner@example.test', full_name: 'Tenant Owner', is_active: true, is_superuser: false, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
    error: null, notice: null, login: async () => false, logout: () => undefined, clearError: () => undefined, authenticatedRequest: request,
  }
  function Harness() {
    const value = useMemo(() => auth, [])
    return <AuthContext.Provider value={value}><ActiveTenantProvider><TenantControls /><DocumentsPage /></ActiveTenantProvider></AuthContext.Provider>
  }
  const view = render(<BrowserRouter><QueryClientProvider client={queryClient}><Harness /></QueryClientProvider></BrowserRouter>)
  return { ...view, queryClient }
}

async function openTenantPermissions(tenantName: string, docId: string, waitForPermissions = true) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: `Switch to ${tenantName}` }))
  expect(await screen.findByText('Tenant policy.pdf')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Manage permissions' }))
  if (waitForPermissions) expect(await screen.findByRole('region', { name: `Permissions for document ${docId}` })).toBeInTheDocument()
  else expect(await screen.findByText('Loading document permissions')).toBeInTheDocument()
  return user
}

describe('permission tenant isolation through ActiveTenantProvider and DocumentsPage', () => {
  it('caches Tenant A permissions under its key, removes them on switch, and only renders Tenant B data', async () => {
    const { queryClient } = renderWorkspace(async (tenantId, docId) => tenantId === tenantA.tenant.id
      ? permissionResponse(tenantId, docId, 'CONFIDENTIAL', 'HR')
      : permissionResponse(tenantId, docId, 'RESTRICTED', 'EMPLOYEE'))
    await openTenantPermissions('Tenant A', documentA)
    expect(await screen.findByLabelText('Classification')).toHaveValue('CONFIDENTIAL')
    const keyA = tenantQueryKey(tenantA.tenant.id, 'documents', 'permissions', documentA)
    expect(queryClient.getQueryData(keyA)).toMatchObject(permissionResponse(tenantA.tenant.id, documentA, 'CONFIDENTIAL', 'HR'))

    await userEvent.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(await screen.findByText('Tenant policy.pdf')).toBeInTheDocument()
    expect(queryClient.getQueryData(keyA)).toBeUndefined()
    await userEvent.click(screen.getByRole('button', { name: 'Manage permissions' }))
    expect(await screen.findByRole('region', { name: `Permissions for document ${documentB}` })).toBeInTheDocument()
    expect(await screen.findByLabelText('Classification')).toHaveValue('RESTRICTED')
    expect(screen.getByLabelText('EMPLOYEE', { selector: 'input' })).toBeChecked()
    expect(screen.queryByLabelText('HR', { selector: 'input' })).not.toBeChecked()
  })

  it('aborts an unresolved Tenant A permission request and loads Tenant B permissions normally', async () => {
    let signalA: AbortSignal | undefined
    let resolveA!: (value: unknown) => void
    const { queryClient } = renderWorkspace((tenantId, docId, signal) => {
      if (tenantId === tenantA.tenant.id) {
        signalA = signal
        return new Promise((resolve) => { resolveA = resolve })
      }
      return Promise.resolve(permissionResponse(tenantId, docId, 'PUBLIC', 'MANAGER'))
    })
    await openTenantPermissions('Tenant A', documentA, false)
    expect(signalA?.aborted).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(signalA?.aborted).toBe(true)
    expect(await screen.findByText('Tenant policy.pdf')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Manage permissions' }))
    expect(await screen.findByLabelText('Classification')).toHaveValue('PUBLIC')
    await act(async () => { resolveA(permissionResponse(tenantA.tenant.id, documentA, 'CONFIDENTIAL', 'HR')) })
    expect(queryClient.getQueryData(tenantQueryKey(tenantA.tenant.id, 'documents', 'permissions', documentA))).toBeUndefined()
    expect(queryClient.getQueryData(tenantQueryKey(tenantB.tenant.id, 'documents', 'permissions', documentB))).toMatchObject({ classification: 'PUBLIC' })
  })

  it('discards a late Tenant A permission response after Tenant B permissions are visible', async () => {
    let resolveA!: (value: unknown) => void
    let startedA!: () => void
    const started = new Promise<void>((resolve) => { startedA = resolve })
    const { queryClient } = renderWorkspace((tenantId, docId) => tenantId === tenantA.tenant.id
      ? new Promise((resolve) => { resolveA = resolve; startedA() })
      : Promise.resolve(permissionResponse(tenantId, docId, 'INTERNAL', 'OWNER')))
    const user = await openTenantPermissions('Tenant A', documentA, false)
    await started
    await user.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(await screen.findByText('Tenant policy.pdf')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Manage permissions' }))
    expect(await screen.findByLabelText('Classification')).toHaveValue('INTERNAL')

    await act(async () => { resolveA(permissionResponse(tenantA.tenant.id, documentA, 'CONFIDENTIAL', 'HR')) })
    expect(screen.getByLabelText('Active tenant')).toHaveTextContent('Tenant B')
    expect(screen.getByLabelText('Classification')).toHaveValue('INTERNAL')
    expect(queryClient.getQueryData(tenantQueryKey(tenantA.tenant.id, 'documents', 'permissions', documentA))).toBeUndefined()
    expect(queryClient.getQueryData(tenantQueryKey(tenantB.tenant.id, 'documents', 'permissions', documentB))).toMatchObject({ classification: 'INTERNAL' })
  })

  it('keeps Tenant A ACL out of Tenant B while a Tenant B 503 is being reconciled', async () => {
    let tenantBReads = 0
    const tenantBCurrent = permissionResponse(tenantB.tenant.id, documentB, 'RESTRICTED', 'EMPLOYEE')
    const tenantBReconciled = permissionResponse(tenantB.tenant.id, documentB, 'PUBLIC', 'MANAGER')
    const { queryClient } = renderWorkspace(async (tenantId, docId, _signal, method) => {
      if (method === 'PUT') throw new ApiError('sensitive permission sync detail', 'http', 503)
      if (tenantId === tenantA.tenant.id) return permissionResponse(tenantId, docId, 'CONFIDENTIAL', 'HR')
      tenantBReads += 1
      return tenantBReads === 1 ? tenantBCurrent : tenantBReconciled
    })
    await openTenantPermissions('Tenant A', documentA)
    expect(await screen.findByLabelText('Classification')).toHaveValue('CONFIDENTIAL')
    await userEvent.click(screen.getByRole('button', { name: 'Switch to Tenant B' }))
    expect(await screen.findByText('Tenant policy.pdf')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Manage permissions' }))
    expect(await screen.findByLabelText('Classification')).toHaveValue('RESTRICTED')

    await userEvent.selectOptions(screen.getByLabelText('Classification'), 'INTERNAL')
    await userEvent.click(screen.getByRole('button', { name: 'Review complete ACL' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))
    expect(await screen.findByText(/current permissions have been reloaded/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Active tenant')).toHaveTextContent('Tenant B')
    expect(screen.getByLabelText('Classification')).toHaveValue('PUBLIC')
    expect(screen.getByLabelText('MANAGER', { selector: 'input' })).toBeChecked()
    expect(screen.queryByLabelText('HR', { selector: 'input' })).not.toBeChecked()
    expect(queryClient.getQueryData(tenantQueryKey(tenantA.tenant.id, 'documents', 'permissions', documentA))).toBeUndefined()
    expect(queryClient.getQueryData(tenantQueryKey(tenantB.tenant.id, 'documents', 'permissions', documentB))).toMatchObject({ classification: 'PUBLIC' })
  })
})
