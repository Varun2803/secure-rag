import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { RequireAuth, PublicOnlyRoute } from '@/components/shared/auth-route-guards'
import { AppShell } from '@/components/layout/app-shell'
import { NotFoundPage } from '@/components/shared/not-found-page'
import { FoundationPage } from '@/components/shared/foundation-page'
import { AuthProvider } from '@/features/auth/auth-provider'
import { LoginPage } from '@/features/auth/login-page'
import { RegisterPage } from '@/features/auth/register-page'

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route element={<PublicOnlyRoute />}>
            <Route path="login" element={<LoginPage />} />
            <Route path="register" element={<RegisterPage />} />
          </Route>
          <Route element={<RequireAuth />}>
            <Route element={<AppShell />}>
              <Route index element={<FoundationPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
