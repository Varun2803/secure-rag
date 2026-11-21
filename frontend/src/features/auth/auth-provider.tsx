import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PropsWithChildren } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError, apiRequest, subscribeToUnauthorized } from '@/lib/api'
import type { ApiRequestOptions } from '@/lib/api'
import { AuthContext } from './auth-context'
import type { AuthContextValue, AuthStatus } from './auth-context'
import { fetchCurrentUser, loginUser } from './api'
import type { UserPublic } from './api'
import { internalPathFrom } from './safe-redirect'

type AuthState = {
  status: AuthStatus
  user: UserPublic | null
  error: string | null
  notice: string | null
  expiresAt: number | null
}

const initialState: AuthState = { status: 'unauthenticated', user: null, error: null, notice: null, expiresAt: null }

function authErrorMessage(error: unknown, stage: 'credentials' | 'profile'): string {
  if (stage === 'profile') return 'Sign-in could not be completed. Please try again.'
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Email or password is incorrect.'
    if (error.status === 503) return 'Authentication is temporarily unavailable. Please try again.'
    if (error.kind === 'network') return error.message
  }
  return 'Sign-in could not be completed. Please try again.'
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [state, setState] = useState<AuthState>(initialState)
  const tokenRef = useRef<string | null>(null)
  const attemptRef = useRef(0)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()

  const expireSession = useCallback((failedToken?: string) => {
    if (!tokenRef.current || (failedToken && tokenRef.current !== failedToken)) return
    tokenRef.current = null
    attemptRef.current += 1
    queryClient.clear()
    setState({ status: 'expired', user: null, error: null, notice: null, expiresAt: null })
    navigate('/login', { replace: true, state: { reason: 'expired' } })
  }, [navigate, queryClient])

  useEffect(() => subscribeToUnauthorized((failedToken) => expireSession(failedToken)), [expireSession])

  useEffect(() => {
    if (state.status !== 'authenticated' || state.expiresAt === null) return
    const delay = Math.max(0, state.expiresAt - Date.now())
    const timer = window.setTimeout(() => expireSession(tokenRef.current ?? undefined), delay)
    return () => window.clearTimeout(timer)
  }, [expireSession, state.expiresAt, state.status])

  const login = useCallback(async (email: string, password: string, returnTo?: string | null): Promise<boolean> => {
    const attempt = ++attemptRef.current
    tokenRef.current = null
    queryClient.clear()
    setState({ status: 'authenticating', user: null, error: null, notice: null, expiresAt: null })

    let stage: 'credentials' | 'profile' = 'credentials'
    try {
      const tokenResponse = await loginUser(email, password)
      if (attempt !== attemptRef.current) return false
      tokenRef.current = tokenResponse.access_token
      stage = 'profile'
      const user = await fetchCurrentUser(tokenResponse.access_token)
      if (attempt !== attemptRef.current || tokenRef.current !== tokenResponse.access_token) return false
      if (!user.is_active) throw new Error('Inactive profile response')

      setState({
        status: 'authenticated',
        user,
        error: null,
        notice: null,
        expiresAt: Date.now() + tokenResponse.expires_in * 1000,
      })
      navigate(internalPathFrom(returnTo ? { from: returnTo } : location.state) ?? '/', { replace: true })
      return true
    } catch (error) {
      if (attempt !== attemptRef.current) return false
      tokenRef.current = null
      queryClient.clear()
      setState({ status: 'unauthenticated', user: null, error: authErrorMessage(error, stage), notice: null, expiresAt: null })
      return false
    }
  }, [location.state, navigate, queryClient])

  const logout = useCallback(() => {
    attemptRef.current += 1
    tokenRef.current = null
    queryClient.clear()
    setState({ ...initialState, notice: 'You signed out on this device. The issued access token remains valid until its expiry.' })
    navigate('/login', { replace: true, state: { reason: 'local-logout' } })
  }, [navigate, queryClient])

  const clearError = useCallback(() => {
    setState((current) => current.error ? { ...current, error: null } : current)
  }, [])

  const authenticatedRequest = useCallback(<T,>(path: string, options: Omit<ApiRequestOptions, 'accessToken' | 'notifyUnauthorized'> = {}) => {
    const token = tokenRef.current
    if (!token || state.status !== 'authenticated') {
      return Promise.reject(new ApiError('Authentication is required or has expired.', 'http', 401))
    }
    return apiRequest<T>(path, { ...options, accessToken: token })
  }, [state.status])

  const value = useMemo<AuthContextValue>(() => ({
    status: state.status,
    user: state.user,
    error: state.error,
    notice: state.notice,
    login,
    logout,
    clearError,
    authenticatedRequest,
  }), [authenticatedRequest, clearError, login, logout, state.error, state.notice, state.status, state.user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
