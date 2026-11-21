import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { GlobalLoading } from '@/components/shared/global-loading'
import { useAuth } from '@/features/auth/auth-context'

export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'authenticating') return <GlobalLoading label="Verifying your account" />
  if (status !== 'authenticated') {
    const from = `${location.pathname}${location.search}${location.hash}`
    return <Navigate to={`/login?returnTo=${encodeURIComponent(from)}`} replace />
  }
  return <Outlet />
}

export function PublicOnlyRoute() {
  const { status } = useAuth()
  if (status === 'authenticating') return <GlobalLoading label="Completing sign-in" />
  if (status === 'authenticated') return <Navigate to="/" replace />
  return <Outlet />
}
