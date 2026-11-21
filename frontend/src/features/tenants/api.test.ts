import { describe, expect, it } from 'vitest'
import type { AuthenticatedRequest } from './api'
import { addMembership, createTenant, getTenant, listMemberships, listTenants, updateMembership, updateTenant } from './api'

const tenant = {
  id: '11111111-1111-4111-8111-111111111111', name: 'Example Co', slug: 'example-co', description: null,
  is_active: true, created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z',
}
const member = {
  id: '22222222-2222-4222-8222-222222222222', tenant_id: tenant.id,
  user_id: '33333333-3333-4333-8333-333333333333', role: 'EMPLOYEE', is_active: true,
  joined_at: '2026-11-21T10:00:00Z', created_at: '2026-11-21T10:00:00Z',
  user: { id: '33333333-3333-4333-8333-333333333333', email: 'member@example.test', full_name: 'Member', is_active: true },
}

function requestStub(responses: unknown[]) {
  const calls: Array<{ path: string; options?: unknown }> = []
  const request: AuthenticatedRequest = async <T,>(path: string, options?: unknown) => {
    calls.push({ path, options })
    return responses.shift() as T
  }
  return { request, calls }
}

describe('tenant and membership API contracts', () => {
  it('loads tenant memberships through the authenticated tenant list endpoint', async () => {
    const fixture = requestStub([[{ tenant, role: 'OWNER', joined_at: tenant.created_at }]])
    const result = await listTenants(fixture.request)
    expect(result[0]).toMatchObject({ tenant: { id: tenant.id }, role: 'OWNER' })
    expect(fixture.calls[0]?.path).toBe('/tenants')
  })

  it('creates a tenant with only the supported create fields', async () => {
    const fixture = requestStub([tenant])
    await createTenant(fixture.request, { name: tenant.name, slug: tenant.slug, description: null })
    expect(fixture.calls[0]).toEqual({
      path: '/tenants', options: { method: 'POST', json: { name: tenant.name, slug: tenant.slug, description: null } },
    })
  })

  it('loads and updates tenant details using their supported fields and a cancellable signal', async () => {
    const fixture = requestStub([tenant, tenant])
    const controller = new AbortController()
    await getTenant(fixture.request, tenant.id, controller.signal)
    await updateTenant(fixture.request, tenant.id, { name: 'Updated', description: null })
    expect(fixture.calls[0]).toEqual({ path: `/tenants/${tenant.id}`, options: { signal: controller.signal } })
    expect(fixture.calls[1]).toEqual({
      path: `/tenants/${tenant.id}`, options: { method: 'PATCH', json: { name: 'Updated', description: null } },
    })
  })

  it('uses the real membership add and update bodies without client-supplied tenant or actor fields', async () => {
    const fixture = requestStub([member, member, [member]])
    await addMembership(fixture.request, tenant.id, { email: 'member@example.test', role: 'EMPLOYEE' })
    await updateMembership(fixture.request, tenant.id, member.id, { role: 'HR', is_active: false })
    await listMemberships(fixture.request, tenant.id)
    expect(fixture.calls).toEqual([
      { path: `/tenants/${tenant.id}/memberships`, options: { method: 'POST', json: { email: 'member@example.test', role: 'EMPLOYEE' } } },
      { path: `/tenants/${tenant.id}/memberships/${member.id}`, options: { method: 'PATCH', json: { role: 'HR', is_active: false } } },
      { path: `/tenants/${tenant.id}/memberships`, options: { signal: undefined } },
    ])
  })

  it('rejects malformed server response data rather than rendering it as tenant data', async () => {
    const fixture = requestStub([[{ tenant: { ...tenant, id: 'not-a-uuid' }, role: 'OWNER', joined_at: tenant.created_at }]])
    await expect(listTenants(fixture.request)).rejects.toThrow('invalid tenant response')
  })
})
