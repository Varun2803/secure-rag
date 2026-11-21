import { BrowserRouter, Route, Routes } from 'react-router-dom'
import type { PropsWithChildren } from 'react'
import { RequireAuth, PublicOnlyRoute } from '@/components/shared/auth-route-guards'
import { AppShell } from '@/components/layout/app-shell'
import { NotFoundPage } from '@/components/shared/not-found-page'
import { AuthProvider } from '@/features/auth/auth-provider'
import { LoginPage } from '@/features/auth/login-page'
import { RegisterPage } from '@/features/auth/register-page'
import { ActiveTenantProvider } from '@/features/tenants/active-tenant-provider'
import { TenantWorkspacePage } from '@/features/tenants/tenant-workspace-page'
import { MembershipsPage } from '@/features/memberships/memberships-page'
import { useAuth } from '@/features/auth/auth-context'

function TenantSessionBoundary({ children }: PropsWithChildren) {
  const { status, user } = useAuth()
  return <ActiveTenantProvider key={`${status}:${user?.id ?? ''}`}>{children}</ActiveTenantProvider>
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <TenantSessionBoundary>
          <Routes>
            <Route element={<PublicOnlyRoute />}>
              <Route path="login" element={<LoginPage />} />
              <Route path="register" element={<RegisterPage />} />
            </Route>
            <Route element={<RequireAuth />}>
              <Route element={<AppShell />}>
                <Route index element={<TenantWorkspacePage />} />
                <Route path="memberships" element={<MembershipsPage />} />
                <Route path="*" element={<NotFoundPage />} />
              </Route>
            </Route>
          </Routes>
        </TenantSessionBoundary>
      </AuthProvider>
    </BrowserRouter>
  )
}
