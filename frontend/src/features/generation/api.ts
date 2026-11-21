import { z } from 'zod'
import type { AuthenticatedRequest } from '@/features/tenants/api'
import { knowledgeRequestSchema } from '@/features/retrieval/api'

export const generationRequestSchema = knowledgeRequestSchema.extend({ top_k: knowledgeRequestSchema.shape.top_k.default(5) }).strict()
export const citationSchema = z.object({
  document_id: z.uuid(),
  chunk_id: z.uuid(),
  page_numbers: z.array(z.number().int().min(1)),
  relevance_score: z.number().finite(),
})
export const generationResponseSchema = z.object({
  query: z.string(),
  answer: z.string(),
  citations: z.array(citationSchema),
  result_count: z.number().int().nonnegative(),
  grounded: z.boolean(),
})

export type GenerationRequest = z.infer<typeof generationRequestSchema>
export type GenerationRequestInput = Omit<GenerationRequest, 'top_k'> & { top_k?: number }
export type Citation = z.infer<typeof citationSchema>
export type GenerationResponse = z.infer<typeof generationResponseSchema>

function parse<T>(schema: z.ZodType<T>, data: unknown, kind: 'request' | 'response'): T {
  const parsed = schema.safeParse(data)
  if (!parsed.success) throw new Error(kind === 'request' ? 'Review the question and result count.' : 'The service returned an invalid answer response.')
  return parsed.data
}

export async function generateAnswer(request: AuthenticatedRequest, tenantId: string, input: GenerationRequestInput, signal?: AbortSignal): Promise<GenerationResponse> {
  const body = parse(generationRequestSchema, input, 'request')
  return parse(generationResponseSchema, await request(`/tenants/${tenantId}/generation/answer`, { method: 'POST', json: body, signal }), 'response')
}
