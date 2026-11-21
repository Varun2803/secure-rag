import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '@/features/auth/auth-context'
import type { AuthContextValue } from '@/features/auth/auth-context'
import { ApiError } from '@/lib/api/api-error'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { PermissionEditor } from './permission-editor'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const tenantId = '11111111-1111-4111-8111-111111111111'
const documentId = '22222222-2222-4222-8222-222222222222'
const activeUser = '33333333-3333-4333-8333-333333333333'
const existing = { document_id: documentId, tenant_id: tenantId, classification: 'CONFIDENTIAL', is_public: false, allowed_roles: ['HR'], allowed_users: [activeUser], permission_updated_at: '2026-11-21T10:00:00Z', synchronization_status: 'PENDING' }
const memberships = [
  { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', tenant_id: tenantId, user_id: activeUser, role: 'EMPLOYEE', is_active: true, joined_at: '2026-11-20T10:00:00Z', created_at: '2026-11-20T10:00:00Z', user: { id: activeUser, email: 'member@example.test', full_name: 'Tenant Member', is_active: true } },
  { id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', tenant_id: tenantId, user_id: '44444444-4444-4444-8444-444444444444', role: 'EMPLOYEE', is_active: false, joined_at: '2026-11-20T10:00:00Z', created_at: '2026-11-20T10:00:00Z', user: { id: '44444444-4444-4444-8444-444444444444', email: 'inactive@example.test', full_name: 'Inactive Person', is_active: true } },
]

function renderEditor(role: 'OWNER' | 'ADMIN' | 'HR' | 'MANAGER' | 'EMPLOYEE', request: AuthContextValue['authenticatedRequest']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const auth: AuthContextValue = {
    status: 'authenticated', user: { id: '55555555-5555-4555-8555-555555555555', email: 'admin@example.test', full_name: 'Tenant Admin', is_active: true, is_superuser: false, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' },
    error: null, notice: null, login: async () => false, logout: () => undefined, clearError: () => undefined, authenticatedRequest: request,
  }
  const view = render(<QueryClientProvider client={queryClient}><AuthContext.Provider value={auth}><PermissionEditor tenantId={tenantId} documentId={documentId} tenantRole={role} /></AuthContext.Provider></QueryClientProvider>)
  return { ...view, queryClient }
}

function permissionRequest() {
  return vi.fn(async (path: string, options?: { method?: string }) => {
    if (path.endsWith('/permissions') && !options?.method) return existing
    if (path.endsWith('/memberships')) return memberships
    if (options?.method === 'PUT') return { ...existing, classification: 'INTERNAL', is_public: true, allowed_roles: ['HR', 'EMPLOYEE'], synchronization_status: 'COMPLETED' }
    throw new Error(`Unsupported endpoint called: ${path}`)
  }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
}

describe('permission editor', () => {
  it.each(['OWNER', 'ADMIN'] as const)('%s can read and replace complete permissions with confirmation', async (role) => {
    const request = permissionRequest()
    const { queryClient } = renderEditor(role, request)
    expect(await screen.findByRole('region', { name: `Permissions for document ${documentId}` })).toBeInTheDocument()
    expect(screen.getByLabelText('Classification')).toHaveValue('CONFIDENTIAL')
    expect(screen.getByText('Synchronization pending')).toBeInTheDocument()
    expect(screen.getByText('Tenant Member')).toBeInTheDocument()
    expect(screen.queryByText('Inactive Person')).not.toBeInTheDocument()
    expect(screen.getByLabelText('HR', { selector: 'input' })).toBeChecked()
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}/permissions`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(queryClient.getQueryData(tenantQueryKey(tenantId, 'documents', 'permissions', documentId))).toMatchObject(existing)
    expect(queryClient.getQueryData(tenantQueryKey('99999999-9999-4999-8999-999999999999', 'documents', 'permissions', documentId))).toBeUndefined()

    await userEvent.selectOptions(screen.getByLabelText('Classification'), 'INTERNAL')
    await userEvent.click(screen.getByRole('radio', { name: /Public within this tenant/ }))
    await userEvent.click(screen.getByLabelText('EMPLOYEE', { selector: 'input' }))
    await userEvent.click(screen.getByRole('button', { name: 'Review complete ACL' }))
    expect(screen.getByRole('dialog', { name: 'Replace document permissions?' })).toBeInTheDocument()
    expect(request).not.toHaveBeenCalledWith(expect.stringContaining('/permissions'), expect.objectContaining({ method: 'PUT' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))
    expect(await screen.findByText('Synchronized')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}/permissions`, {
      method: 'PUT', json: { classification: 'INTERNAL', is_public: true, allowed_roles: ['HR', 'EMPLOYEE'], allowed_users: [activeUser] },
    })
  })

  it.each(['HR', 'MANAGER', 'EMPLOYEE'] as const)('%s does not see controls or trigger permission/member requests', async (role) => {
    const request = permissionRequest()
    const { container } = renderEditor(role, request)
    expect(container).toBeEmptyDOMElement()
    expect(request).not.toHaveBeenCalled()
  })

  it.each([
    [401, 'sign-in is required or has expired'],
    [403, 'Only tenant OWNERs and ADMINs'],
    [404, 'tenant or document is unavailable'],
  ] as const)('shows a sanitized permission read error for HTTP %i', async (status, message) => {
    const request = vi.fn(async () => { throw new ApiError('sensitive service detail', 'http', status) }) as unknown as AuthContextValue['authenticatedRequest']
    renderEditor('ADMIN', request)
    expect(await screen.findByText(message, { exact: false })).toBeInTheDocument()
    expect(screen.queryByText('sensitive service detail')).not.toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(`/tenants/${tenantId}/documents/${documentId}/permissions`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('supports a deny-all private ACL and reports sanitized errors', async () => {
    const request = vi.fn(async (path: string, options?: { method?: string }) => {
      if (path.endsWith('/permissions') && !options?.method) return { ...existing, allowed_roles: [], allowed_users: [] }
      if (path.endsWith('/memberships')) return memberships
      if (options?.method === 'PUT') throw new ApiError('sensitive service message', 'http', 422)
      throw new Error(`Unexpected endpoint ${path}`)
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderEditor('OWNER', request)
    await screen.findByRole('region', { name: `Permissions for document ${documentId}` })
    expect(screen.getByRole('radio', { name: /Private/ })).toBeChecked()
    expect(screen.getByText(/no role or user grants denies content access/i)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Classification'), 'INTERNAL')
    await userEvent.click(screen.getByRole('button', { name: 'Review complete ACL' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('selected roles or users are invalid')
    expect(alert).not.toHaveTextContent('sensitive service message')
  })

  it('reconciles an ambiguous PUT 503 without retrying the PUT and shows the authoritative ACL', async () => {
    let permissionGets = 0
    let permissionPuts = 0
    const reconciled = { ...existing, classification: 'RESTRICTED', allowed_roles: ['MANAGER'], allowed_users: [], synchronization_status: 'PROCESSING' }
    const request = vi.fn(async (path: string, options?: { method?: string }) => {
      if (path.endsWith('/permissions') && !options?.method) return ++permissionGets === 1 ? existing : reconciled
      if (path.endsWith('/memberships')) return memberships
      if (options?.method === 'PUT') { permissionPuts += 1; throw new ApiError('sensitive sync persistence detail', 'http', 503) }
      throw new Error(`Unexpected endpoint ${path}`)
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderEditor('ADMIN', request)
    await screen.findByRole('region', { name: `Permissions for document ${documentId}` })
    await userEvent.selectOptions(screen.getByLabelText('Classification'), 'INTERNAL')
    await userEvent.click(screen.getByRole('button', { name: 'Review complete ACL' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))

    expect(await screen.findByText(/save outcome was not confirmed/i)).toBeInTheDocument()
    expect(await screen.findByText('Synchronization processing')).toBeInTheDocument()
    expect(screen.getByLabelText('Classification')).toHaveValue('RESTRICTED')
    expect(screen.getByLabelText('MANAGER', { selector: 'input' })).toBeChecked()
    expect(screen.queryByText(/permission change could not be saved/i)).not.toBeInTheDocument()
    expect(screen.queryByText('sensitive sync persistence detail')).not.toBeInTheDocument()
    expect(permissionGets).toBe(2)
    expect(permissionPuts).toBe(1)
  })

  it('retains edits after reconciliation GET failure and allows manual reload without retrying PUT', async () => {
    let permissionGets = 0
    let permissionPuts = 0
    const latest = { ...existing, classification: 'RESTRICTED', allowed_roles: [], allowed_users: [], synchronization_status: 'COMPLETED' }
    const request = vi.fn(async (path: string, options?: { method?: string }) => {
      if (path.endsWith('/permissions') && !options?.method) {
        permissionGets += 1
        if (permissionGets === 1) return existing
        if (permissionGets === 2) throw new ApiError('sensitive reload detail', 'http', 503)
        return latest
      }
      if (path.endsWith('/memberships')) return memberships
      if (options?.method === 'PUT') { permissionPuts += 1; throw new ApiError('sensitive update detail', 'http', 503) }
      throw new Error(`Unexpected endpoint ${path}`)
    }) as unknown as AuthContextValue['authenticatedRequest'] & ReturnType<typeof vi.fn>
    renderEditor('OWNER', request)
    await screen.findByRole('region', { name: `Permissions for document ${documentId}` })
    await userEvent.selectOptions(screen.getByLabelText('Classification'), 'INTERNAL')
    await userEvent.click(screen.getByRole('button', { name: 'Review complete ACL' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))

    expect(await screen.findByRole('button', { name: 'Reload current permissions' })).toBeInTheDocument()
    expect(screen.getByText(/Your edits remain in the form/)).toBeInTheDocument()
    expect(screen.getByLabelText('Classification')).toHaveValue('INTERNAL')
    expect(permissionPuts).toBe(1)

    await userEvent.click(screen.getByRole('button', { name: 'Reload current permissions' }))
    expect(await screen.findByText(/current permissions have been reloaded/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Classification')).toHaveValue('RESTRICTED')
    expect(screen.getByText('Synchronized')).toBeInTheDocument()
    expect(permissionGets).toBe(3)
    expect(permissionPuts).toBe(1)
  })
})
