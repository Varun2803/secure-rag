import { createContext, useContext } from 'react'
import type { ApiRequestOptions } from '@/lib/api/client'
import type { UserPublic } from './api'

export type AuthStatus = 'unauthenticated' | 'authenticating' | 'authenticated' | 'expired'

export type AuthContextValue = {
  status: AuthStatus
  user: UserPublic | null
  error: string | null
  notice: string | null
  login: (email: string, password: string, returnTo?: string | null) => Promise<boolean>
  logout: () => void
  clearError: () => void
  authenticatedRequest: <T>(path: string, options?: Omit<ApiRequestOptions, 'accessToken' | 'notifyUnauthorized'>) => Promise<T>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
