import { z } from 'zod'
import type { AuthenticatedRequest, TenantRole } from '@/features/tenants/api'

export const documentPermissionRoles = ['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE'] as const
export const permissionClassificationSchema = z.enum(['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED'])
export const permissionSyncStatusSchema = z.enum(['PENDING', 'PROCESSING', 'COMPLETED'])

export const documentPermissionsSchema = z.object({
  document_id: z.uuid(),
  tenant_id: z.uuid(),
  classification: permissionClassificationSchema,
  is_public: z.boolean(),
  allowed_roles: z.array(z.enum(documentPermissionRoles)),
  allowed_users: z.array(z.uuid()),
  permission_updated_at: z.iso.datetime({ offset: true }).nullable(),
  synchronization_status: permissionSyncStatusSchema,
})

export const permissionReplacementSchema = z.object({
  classification: permissionClassificationSchema,
  is_public: z.boolean(),
  allowed_roles: z.array(z.enum(documentPermissionRoles)).refine((roles) => new Set(roles).size === roles.length, 'Remove duplicate roles.'),
  allowed_users: z.array(z.uuid()).refine((users) => new Set(users).size === users.length, 'Remove duplicate users.'),
}).strict()

export type DocumentPermissions = z.infer<typeof documentPermissionsSchema>
export type PermissionReplacement = z.infer<typeof permissionReplacementSchema>
export type PermissionRole = (typeof documentPermissionRoles)[number]

function parse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data)
  if (!result.success) throw new Error('The service returned an invalid permission response.')
  return result.data
}

function permissionsPath(tenantId: string, documentId: string) {
  return `/tenants/${tenantId}/documents/${documentId}/permissions`
}

export async function getDocumentPermissions(request: AuthenticatedRequest, tenantId: string, documentId: string, signal?: AbortSignal): Promise<DocumentPermissions> {
  return parse(documentPermissionsSchema, await request(permissionsPath(tenantId, documentId), { signal }))
}

export async function replaceDocumentPermissions(request: AuthenticatedRequest, tenantId: string, documentId: string, input: PermissionReplacement): Promise<DocumentPermissions> {
  const payload = parse(permissionReplacementSchema, input)
  return parse(documentPermissionsSchema, await request(permissionsPath(tenantId, documentId), { method: 'PUT', json: payload }))
}

export function isTenantPermissionManager(role: TenantRole | undefined): boolean {
  return role === 'OWNER' || role === 'ADMIN'
}
