import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useLocation } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuth } from './auth-context'
import { AuthPageLayout } from './auth-page-layout'
import { PasswordField } from './password-field'

const loginSchema = z.object({
  email: z.email('Enter a valid email address.'),
  password: z.string().min(1, 'Enter your password.'),
})

type LoginFields = z.infer<typeof loginSchema>

function routeNotice(state: unknown): string | null {
  if (!state || typeof state !== 'object' || !('reason' in state)) return null
  const reason = (state as { reason?: unknown }).reason
  if (reason === 'expired') return 'Your session expired. Sign in again to continue.'
  if (reason === 'local-logout') return 'You signed out on this device. The issued access token remains valid until its expiry.'
  if (reason === 'registration-success') return 'Your account is ready. Sign in to continue.'
  return null
}

export function LoginPage() {
  const { login, error: authError, notice: authNotice, clearError, status } = useAuth()
  const location = useLocation()
  const notice = authNotice ?? routeNotice(location.state)
  const form = useForm<LoginFields>({ resolver: zodResolver(loginSchema), mode: 'onBlur' })
  const emailError = form.formState.errors.email?.message
  const passwordError = form.formState.errors.password?.message

  async function onSubmit(values: LoginFields) {
    clearError()
    const returnTo = new URLSearchParams(location.search).get('returnTo')
    const authenticated = await login(values.email.trim(), values.password, returnTo)
    if (authenticated) form.reset({ email: values.email, password: '' })
  }

  return (
    <AuthPageLayout eyebrow="Welcome back" title="Sign in to SecureRAG" description="Use your account credentials to continue to your workspace.">
      {notice && <p className="mb-5 rounded-lg border border-primary/20 bg-accent px-3 py-2.5 text-sm text-accent-foreground" role="status">{notice}</p>}
      {authError && <p className="mb-5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm text-destructive" role="alert">{authError}</p>}

      <form className="space-y-5" noValidate onSubmit={form.handleSubmit(onSubmit)}>
        <div>
          <label className="mb-1.5 block text-sm font-medium" htmlFor="login-email">Email</label>
          <Input
            {...form.register('email', { onChange: clearError })}
            id="login-email"
            autoComplete="email"
            autoCapitalize="none"
            inputMode="email"
            type="email"
            aria-invalid={Boolean(emailError)}
            aria-describedby={emailError ? 'login-email-error' : undefined}
          />
          {emailError && <p className="mt-1.5 text-xs text-destructive" id="login-email-error">{emailError}</p>}
        </div>

        <div>
          <PasswordField
            id="login-password"
            label="Password"
            autoComplete="current-password"
            invalid={Boolean(passwordError)}
            describedBy={passwordError ? 'login-password-error' : undefined}
            name="password"
            register={form.register}
            onChange={clearError}
          />
          {passwordError && <p className="mt-1.5 text-xs text-destructive" id="login-password-error">{passwordError}</p>}
        </div>

        <Button className="w-full" disabled={form.formState.isSubmitting || status === 'authenticating'} type="submit">
          {form.formState.isSubmitting || status === 'authenticating' ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>

      <p className="mt-6 text-center text-sm text-muted-foreground">
        New to SecureRAG? <Link className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" to="/register">Create an account</Link>
      </p>
    </AuthPageLayout>
  )
}
