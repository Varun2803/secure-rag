import { z } from 'zod'
import type { AuthenticatedRequest } from '@/features/tenants/api'

export const documentStatuses = ['PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'] as const
export const documentStatusSchema = z.enum(documentStatuses)
export const documentClassificationSchema = z.union([z.literal(0), z.literal(1), z.literal(2), z.literal(3)])

export const documentSchema = z.object({
  id: z.uuid(),
  tenant_id: z.uuid(),
  uploaded_by: z.uuid(),
  filename: z.string(),
  original_filename: z.string(),
  file_hash: z.string(),
  file_size: z.number().int().nonnegative(),
  content_type: z.string(),
  page_count: z.number().int().nonnegative(),
  chunk_count: z.number().int().nonnegative(),
  status: documentStatusSchema,
  classification: documentClassificationSchema,
  is_public: z.boolean(),
  allowed_roles: z.array(z.string()),
  allowed_users: z.array(z.string()),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
  processing_completed_at: z.iso.datetime({ offset: true }).nullable(),
})

export const documentPageSchema = z.object({
  items: z.array(documentSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().nonnegative(),
})

export const documentProcessingStatusSchema = z.object({
  id: z.uuid(),
  status: documentStatusSchema,
  page_count: z.number().int().nonnegative(),
  chunk_count: z.number().int().nonnegative(),
  failure_detail: z.string().nullable(),
})

export type DocumentRecord = z.infer<typeof documentSchema>
export type DocumentPage = z.infer<typeof documentPageSchema>
export type DocumentProcessingStatus = z.infer<typeof documentProcessingStatusSchema>

function parse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data)
  if (!result.success) throw new Error('The service returned an invalid document response.')
  return result.data
}

function documentsPath(tenantId: string) {
  return `/tenants/${tenantId}/documents`
}

export async function listDocuments(
  request: AuthenticatedRequest,
  tenantId: string,
  limit: number,
  offset: number,
  signal?: AbortSignal,
): Promise<DocumentPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return parse(documentPageSchema, await request(`${documentsPath(tenantId)}?${query}`, { signal }))
}

export async function getDocument(request: AuthenticatedRequest, tenantId: string, documentId: string, signal?: AbortSignal): Promise<DocumentRecord> {
  return parse(documentSchema, await request(`${documentsPath(tenantId)}/${documentId}`, { signal }))
}

export async function getDocumentStatus(request: AuthenticatedRequest, tenantId: string, documentId: string, signal?: AbortSignal): Promise<DocumentProcessingStatus> {
  return parse(documentProcessingStatusSchema, await request(`${documentsPath(tenantId)}/${documentId}/status`, { signal }))
}

export async function uploadDocument(request: AuthenticatedRequest, tenantId: string, file: File): Promise<DocumentRecord> {
  const formData = new FormData()
  formData.append('file', file)
  return parse(documentSchema, await request(`${documentsPath(tenantId)}/upload`, { method: 'POST', formData }))
}
