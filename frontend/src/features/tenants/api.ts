import { z } from 'zod'
import type { ApiRequestOptions } from '@/lib/api/client'

const uuidSchema = z.uuid()
const roleSchema = z.enum(['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE'])

export const tenantSchema = z.object({
  id: uuidSchema,
  name: z.string(),
  slug: z.string(),
  description: z.string().nullable(),
  is_active: z.boolean(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
})

export const tenantListItemSchema = z.object({
  tenant: tenantSchema,
  role: roleSchema,
  joined_at: z.iso.datetime({ offset: true }),
})

const tenantListSchema = z.array(tenantListItemSchema)
const publicUserSchema = z.object({
  id: uuidSchema,
  email: z.email(),
  full_name: z.string(),
  is_active: z.boolean(),
})

export const membershipSchema = z.object({
  id: uuidSchema,
  tenant_id: uuidSchema,
  user_id: uuidSchema,
  role: roleSchema,
  is_active: z.boolean(),
  joined_at: z.iso.datetime({ offset: true }),
  created_at: z.iso.datetime({ offset: true }),
  user: publicUserSchema,
})

export type Tenant = z.infer<typeof tenantSchema>
export type TenantListItem = z.infer<typeof tenantListItemSchema>
export type TenantRole = z.infer<typeof roleSchema>
export type Membership = z.infer<typeof membershipSchema>

export type AuthenticatedRequest = <T>(path: string, options?: Omit<ApiRequestOptions, 'accessToken' | 'notifyUnauthorized'>) => Promise<T>

function parse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data)
  if (!result.success) throw new Error('The service returned an invalid tenant response.')
  return result.data
}

export async function listTenants(request: AuthenticatedRequest, signal?: AbortSignal): Promise<TenantListItem[]> {
  return parse(tenantListSchema, await request('/tenants', { signal }))
}

export async function createTenant(request: AuthenticatedRequest, input: { name: string; slug: string; description: string | null }): Promise<Tenant> {
  return parse(tenantSchema, await request('/tenants', { method: 'POST', json: input }))
}

export async function getTenant(request: AuthenticatedRequest, tenantId: string, signal?: AbortSignal): Promise<Tenant> {
  return parse(tenantSchema, await request(`/tenants/${tenantId}`, { signal }))
}

export async function updateTenant(request: AuthenticatedRequest, tenantId: string, input: { name?: string; description?: string | null }): Promise<Tenant> {
  return parse(tenantSchema, await request(`/tenants/${tenantId}`, { method: 'PATCH', json: input }))
}

export async function listMemberships(request: AuthenticatedRequest, tenantId: string, signal?: AbortSignal): Promise<Membership[]> {
  const schema = z.array(membershipSchema)
  return parse(schema, await request(`/tenants/${tenantId}/memberships`, { signal }))
}

export async function addMembership(request: AuthenticatedRequest, tenantId: string, input: { email: string; role: TenantRole }): Promise<Membership> {
  return parse(membershipSchema, await request(`/tenants/${tenantId}/memberships`, { method: 'POST', json: input }))
}

export async function updateMembership(request: AuthenticatedRequest, tenantId: string, membershipId: string, input: { role?: TenantRole; is_active?: boolean }): Promise<Membership> {
  return parse(membershipSchema, await request(`/tenants/${tenantId}/memberships/${membershipId}`, { method: 'PATCH', json: input }))
}
