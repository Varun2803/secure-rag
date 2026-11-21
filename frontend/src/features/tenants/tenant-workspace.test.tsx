import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '@/app/App'
import { AppProviders } from '@/app/providers/app-providers'
import type { Tenant, TenantListItem } from './api'
import { cleanup } from '@testing-library/react'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const token = { access_token: 'in-memory-test-token', token_type: 'bearer', expires_in: 1800 }
const userProfile = {
  id: '33333333-3333-4333-8333-333333333333', email: 'owner@example.test', full_name: 'Workspace Owner',
  is_active: true, is_superuser: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z',
}
const primaryTenant = {
  id: '11111111-1111-4111-8111-111111111111', name: 'Primary Workspace', slug: 'primary-workspace', description: 'Initial description',
  is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z',
}
const existingMembership = {
  id: '22222222-2222-4222-8222-222222222222', tenant_id: primaryTenant.id,
  user_id: '44444444-4444-4444-8444-444444444444', role: 'EMPLOYEE', is_active: true,
  joined_at: '2026-11-21T10:00:00Z', created_at: '2026-11-21T10:00:00Z',
  user: { id: '44444444-4444-4444-8444-444444444444', email: 'member@example.test', full_name: 'Existing Member', is_active: true },
}

async function signIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Email'), 'owner@example.test')
  await user.type(screen.getByLabelText('Password'), 'SecurePassword123!')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('tenant and membership workspace', () => {
  it('selects a returned tenant, creates a workspace, and updates only supported tenant fields', async () => {
    let tenantRows: TenantListItem[] = [{ tenant: primaryTenant, role: 'OWNER', joined_at: primaryTenant.created_at }]
    const tenantRecords = new Map<string, Tenant>([[primaryTenant.id, primaryTenant]])
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/auth/login') return new Response(JSON.stringify(token), { status: 200 })
      if (path === '/api/v1/auth/me') return new Response(JSON.stringify(userProfile), { status: 200 })
      if (path === '/api/v1/tenants' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { name: string; slug: string; description: string | null }
        const created = { ...primaryTenant, id: '55555555-5555-4555-8555-555555555555', ...body, created_at: '2026-11-21T11:00:00Z', updated_at: '2026-11-21T11:00:00Z' }
        tenantRecords.set(created.id, created)
        tenantRows = [...tenantRows, { tenant: created, role: 'OWNER', joined_at: created.created_at }]
        return new Response(JSON.stringify(created), { status: 201 })
      }
      if (path === '/api/v1/tenants') return new Response(JSON.stringify(tenantRows), { status: 200 })
      const detailMatch = path.match(/^\/api\/v1\/tenants\/([^/]+)$/)
      if (detailMatch && init?.method === 'PATCH') {
        const current = tenantRecords.get(detailMatch[1]!)!
        const patch = JSON.parse(String(init.body)) as { name?: string; description?: string | null }
        const updated = { ...current, ...patch, updated_at: '2026-11-21T12:00:00Z' }
        tenantRecords.set(updated.id, updated)
        tenantRows = tenantRows.map((row) => row.tenant.id === updated.id ? { ...row, tenant: updated } : row)
        return new Response(JSON.stringify(updated), { status: 200 })
      }
      if (detailMatch) {
        const current = tenantRecords.get(detailMatch[1]!)
        return current ? new Response(JSON.stringify(current), { status: 200 }) : new Response('{}', { status: 404 })
      }
      throw new Error(`Unexpected mocked request ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<AppProviders><App /></AppProviders>)
    await signIn(user)
    expect(await screen.findByRole('option', { name: /Primary Workspace/ })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Active tenant'), { target: { value: primaryTenant.id } })
    expect(await screen.findByText('Initial description')).toBeInTheDocument()
    expect(screen.getByText('OWNER')).toBeInTheDocument()
    const mobileNavigation = screen.getByRole('navigation', { name: 'Mobile workspace navigation' })
    expect(mobileNavigation).toHaveClass('lg:hidden')
    expect(within(mobileNavigation).getByRole('link', { name: 'Tenant workspace' })).toBeInTheDocument()
    expect(within(mobileNavigation).getByRole('link', { name: 'Manage memberships' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('Tenant name', { selector: 'input#tenant-create-name' }), 'New Workspace')
    await user.type(screen.getByLabelText('Tenant identifier'), 'new_workspace')
    await user.click(screen.getByRole('button', { name: 'Create tenant' }))
    expect(await screen.findByText('Tenant created and selected.')).toBeInTheDocument()
    const createdId = '55555555-5555-4555-8555-555555555555'
    expect(screen.getByLabelText('Active tenant')).toHaveValue(createdId)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/tenants', expect.objectContaining({ method: 'POST' }))

    const nameInput = await screen.findByLabelText('Tenant name', { selector: 'input#tenant-update-name' })
    await user.clear(nameInput)
    await user.type(nameInput, 'Renamed Workspace')
    await user.click(screen.getByRole('button', { name: 'Save tenant details' }))
    expect(await screen.findByText('Tenant details saved.')).toBeInTheDocument()
    const updateCall = fetchMock.mock.calls.find(([path, init]) => String(path) === `/api/v1/tenants/${createdId}` && init?.method === 'PATCH')
    expect(updateCall).toBeDefined()
    const sent = JSON.parse(String(updateCall?.[1]?.body)) as Record<string, unknown>
    expect(sent).toEqual({ name: 'Renamed Workspace' })
    expect(Object.keys(sent)).not.toContain('slug')
  })

  it('uses membership role instead of global superuser status and applies administrator role limits', async () => {
    let members = [existingMembership]
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/auth/login') return new Response(JSON.stringify(token), { status: 200 })
      if (path === '/api/v1/auth/me') return new Response(JSON.stringify(userProfile), { status: 200 })
      if (path === '/api/v1/tenants') return new Response(JSON.stringify([{ tenant: primaryTenant, role: 'ADMIN', joined_at: primaryTenant.created_at }]), { status: 200 })
      if (path === `/api/v1/tenants/${primaryTenant.id}`) return new Response(JSON.stringify(primaryTenant), { status: 200 })
      if (path === `/api/v1/tenants/${primaryTenant.id}/memberships` && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { email: string; role: string }
        const created = { ...existingMembership, id: '66666666-6666-4666-8666-666666666666', email: undefined, role: body.role, user_id: '77777777-7777-4777-8777-777777777777', user: { id: '77777777-7777-4777-8777-777777777777', email: body.email, full_name: 'New Member', is_active: true } }
        members = [...members, created]
        return new Response(JSON.stringify(created), { status: 201 })
      }
      if (path === `/api/v1/tenants/${primaryTenant.id}/memberships`) return new Response(JSON.stringify(members), { status: 200 })
      if (path.startsWith(`/api/v1/tenants/${primaryTenant.id}/memberships/`) && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { role?: string; is_active?: boolean }
        members = members.map((item) => item.id === existingMembership.id ? { ...item, ...body } : item)
        return new Response(JSON.stringify(members[0]), { status: 200 })
      }
      throw new Error(`Unexpected mocked request ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<AppProviders><App /></AppProviders>)
    await signIn(user)
    fireEvent.change(await screen.findByLabelText('Active tenant'), { target: { value: primaryTenant.id } })
    expect(await screen.findByText('Initial description')).toBeInTheDocument()
    await user.click(within(screen.getByRole('navigation', { name: 'Mobile workspace navigation' })).getByRole('link', { name: 'Manage memberships' }))
    expect((await screen.findAllByText('Existing Member')).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('option', { name: 'EMPLOYEE' }).length).toBeGreaterThan(0)
    expect(screen.queryByRole('option', { name: 'OWNER' })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'ADMIN' })).not.toBeInTheDocument()
    expect(screen.queryByText(/superuser/i)).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('Existing user email'), 'new.member@example.test')
    await user.selectOptions(screen.getByLabelText('Tenant role'), 'HR')
    await user.click(screen.getByRole('button', { name: 'Add member' }))
    expect(await screen.findByText('Membership added.')).toBeInTheDocument()
    const addCall = fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/memberships') && init?.method === 'POST')
    expect(JSON.parse(String(addCall?.[1]?.body))).toEqual({ email: 'new.member@example.test', role: 'HR' })

    await user.selectOptions(screen.getByLabelText('Role for Existing Member'), 'MANAGER')
    const existingMemberRow = screen.getByRole('row', { name: /Existing Member/ })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await user.click(within(existingMemberRow).getByRole('button', { name: 'Save' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Existing Member: change the role to MANAGER.'))
    expect((await screen.findAllByText('Existing Member')).length).toBeGreaterThan(0)
    const updateCall = fetchMock.mock.calls.find(([path, init]) => String(path).includes(`/memberships/${existingMembership.id}`) && init?.method === 'PATCH')
    expect(JSON.parse(String(updateCall?.[1]?.body))).toEqual({ role: 'MANAGER' })
  })

  it('cancels and confirms an OWNER privilege change before sending the membership update', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/auth/login') return new Response(JSON.stringify(token), { status: 200 })
      if (path === '/api/v1/auth/me') return new Response(JSON.stringify(userProfile), { status: 200 })
      if (path === '/api/v1/tenants') return new Response(JSON.stringify([{ tenant: primaryTenant, role: 'OWNER', joined_at: primaryTenant.created_at }]), { status: 200 })
      if (path === `/api/v1/tenants/${primaryTenant.id}`) return new Response(JSON.stringify(primaryTenant), { status: 200 })
      if (path === `/api/v1/tenants/${primaryTenant.id}/memberships` && init?.method === 'PATCH') return new Response(JSON.stringify({ ...existingMembership, role: 'ADMIN' }), { status: 200 })
      if (path === `/api/v1/tenants/${primaryTenant.id}/memberships`) return new Response(JSON.stringify([existingMembership]), { status: 200 })
      throw new Error(`Unexpected mocked request ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const user = userEvent.setup()
    render(<AppProviders><App /></AppProviders>)
    await signIn(user)
    fireEvent.change(await screen.findByLabelText('Active tenant'), { target: { value: primaryTenant.id } })
    await screen.findByText('Initial description')
    await user.click(within(screen.getByRole('navigation', { name: 'Mobile workspace navigation' })).getByRole('link', { name: 'Manage memberships' }))
    await screen.findByText('Existing Member')
    const memberRow = screen.getByRole('row', { name: /Existing Member/ })
    await user.selectOptions(screen.getByLabelText('Role for Existing Member'), 'ADMIN')

    await user.click(within(memberRow).getByRole('button', { name: 'Save' }))
    expect(confirm).toHaveBeenNthCalledWith(1, expect.stringContaining('Existing Member: change the role to ADMIN. This grants elevated tenant privileges.'))
    expect(fetchMock.mock.calls.filter(([path, init]) => String(path).includes('/memberships/') && init?.method === 'PATCH')).toHaveLength(0)

    await user.click(within(memberRow).getByRole('button', { name: 'Save' }))
    expect(confirm).toHaveBeenCalledTimes(2)
    const patchCall = fetchMock.mock.calls.find(([path, init]) => String(path).includes(`/memberships/${existingMembership.id}`) && init?.method === 'PATCH')
    expect(patchCall).toBeDefined()
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ role: 'ADMIN' })
  })

  it.each(['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE'] as const)('%s receives only its permitted mobile workspace navigation', async (role) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/v1/auth/login') return new Response(JSON.stringify(token), { status: 200 })
      if (path === '/api/v1/auth/me') return new Response(JSON.stringify(userProfile), { status: 200 })
      if (path === '/api/v1/tenants') return new Response(JSON.stringify([{ tenant: primaryTenant, role, joined_at: primaryTenant.created_at }]), { status: 200 })
      if (path === `/api/v1/tenants/${primaryTenant.id}`) return new Response(JSON.stringify(primaryTenant), { status: 200 })
      if (path === `/api/v1/tenants/${primaryTenant.id}/memberships`) return new Response(JSON.stringify([]), { status: 200 })
      throw new Error(`Unexpected mocked request ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<AppProviders><App /></AppProviders>)
    await signIn(user)
    fireEvent.change(await screen.findByLabelText('Active tenant'), { target: { value: primaryTenant.id } })
    await screen.findByText('Initial description')

    const mobileNavigation = screen.getByRole('navigation', { name: 'Mobile workspace navigation' })
    expect(mobileNavigation).toHaveClass('lg:hidden')
    expect(within(mobileNavigation).getByRole('link', { name: 'Tenant workspace' })).toHaveAttribute('href', '/')
    if (role === 'OWNER' || role === 'ADMIN') {
      await user.click(within(mobileNavigation).getByRole('link', { name: 'Manage memberships' }))
      expect(await screen.findByRole('heading', { name: 'Memberships' })).toBeInTheDocument()
    } else {
      expect(within(mobileNavigation).queryByRole('link', { name: 'Manage memberships' })).not.toBeInTheDocument()
    }
  })
})
