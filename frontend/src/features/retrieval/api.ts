import { z } from 'zod'
import type { AuthenticatedRequest } from '@/features/tenants/api'

export const knowledgeRequestSchema = z.object({
  query: z.string().trim().min(1, 'Enter a search query.').max(2000, 'Queries must be 2,000 characters or fewer.'),
  top_k: z.number().int('Choose a whole number of results.').min(1, 'Choose at least 1 result.').max(20, 'Choose no more than 20 results.'),
}).strict()

export const retrievalRequestSchema = knowledgeRequestSchema.extend({ top_k: knowledgeRequestSchema.shape.top_k.default(5) }).strict()
export const retrievalResultSchema = z.object({
  chunk_id: z.uuid(),
  document_id: z.uuid(),
  chunk_index: z.number().int().nonnegative(),
  page_numbers: z.array(z.number().int().min(1)),
  text: z.string(),
  similarity_score: z.number().finite(),
})
export const retrievalResponseSchema = z.object({
  query: z.string(),
  tenant_id: z.uuid(),
  results: z.array(retrievalResultSchema),
  result_count: z.number().int().nonnegative(),
})

export type KnowledgeRequest = z.infer<typeof retrievalRequestSchema>
export type KnowledgeRequestInput = Omit<KnowledgeRequest, 'top_k'> & { top_k?: number }
export type RetrievalResult = z.infer<typeof retrievalResultSchema>
export type RetrievalResponse = z.infer<typeof retrievalResponseSchema>

function parse<T>(schema: z.ZodType<T>, data: unknown, kind: 'request' | 'response'): T {
  const parsed = schema.safeParse(data)
  if (!parsed.success) throw new Error(kind === 'request' ? 'Review the search query and result count.' : 'The service returned an invalid retrieval response.')
  return parsed.data
}

export async function searchDocuments(request: AuthenticatedRequest, tenantId: string, input: KnowledgeRequestInput, signal?: AbortSignal): Promise<RetrievalResponse> {
  const body = parse(retrievalRequestSchema, input, 'request')
  const response = parse(retrievalResponseSchema, await request(`/tenants/${tenantId}/retrieval/search`, { method: 'POST', json: body, signal }), 'response')
  if (response.tenant_id !== tenantId) throw new Error('The service returned an invalid retrieval response.')
  return response
}
