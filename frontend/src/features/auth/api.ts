import { z } from 'zod'
import { ApiError } from '@/lib/api/api-error'
import { apiRequest } from '@/lib/api/client'

const userPublicSchema = z.object({
  id: z.uuid(),
  email: z.email(),
  full_name: z.string(),
  is_active: z.boolean(),
  is_superuser: z.boolean(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
})

const tokenResponseSchema = z.object({
  access_token: z.string().min(1),
  token_type: z.literal('bearer'),
  expires_in: z.number().int().positive().max(86_400),
})

export type UserPublic = z.infer<typeof userPublicSchema>
export type TokenResponse = z.infer<typeof tokenResponseSchema>

export type RegisterRequest = {
  email: string
  password: string
  full_name: string
}

function parseResponse<T>(schema: z.ZodType<T>, response: unknown): T {
  const result = schema.safeParse(response)
  if (!result.success) {
    throw new ApiError('The service returned an invalid response.', 'invalid-response')
  }
  return result.data
}

export async function registerUser(request: RegisterRequest): Promise<UserPublic> {
  const response = await apiRequest<unknown>('/auth/register', { method: 'POST', json: request })
  return parseResponse(userPublicSchema, response)
}

export async function loginUser(email: string, password: string): Promise<TokenResponse> {
  const response = await apiRequest<unknown>('/auth/login', {
    method: 'POST',
    urlEncoded: { username: email, password },
  })
  return parseResponse(tokenResponseSchema, response)
}

export async function fetchCurrentUser(accessToken: string): Promise<UserPublic> {
  const response = await apiRequest<unknown>('/auth/me', {
    method: 'GET',
    accessToken,
    notifyUnauthorized: false,
  })
  return parseResponse(userPublicSchema, response)
}
