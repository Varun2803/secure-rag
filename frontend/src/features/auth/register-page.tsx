import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ApiError } from '@/lib/api/api-error'
import { registerUser } from './api'
import { AuthPageLayout } from './auth-page-layout'
import { PasswordField } from './password-field'

const registrationSchema = z.object({
  fullName: z.string().trim().min(1, 'Enter your full name.').max(200, 'Full name must be 200 characters or fewer.'),
  email: z.email('Enter a valid email address.'),
  password: z.string()
    .min(12, 'Use at least 12 characters.')
    .max(128, 'Use no more than 128 characters.')
    .refine((value) => value.trim().length > 0, 'Password must not contain only whitespace.'),
  confirmPassword: z.string().min(1, 'Confirm your password.'),
}).refine((values) => values.password === values.confirmPassword, {
  message: 'Passwords do not match.',
  path: ['confirmPassword'],
})

type RegistrationFields = z.infer<typeof registrationSchema>

function registrationErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return 'An account with this email already exists.'
    if (error.status === 422) return 'Check the name, email, and password requirements and try again.'
    if (error.status === 503) return 'Registration is temporarily unavailable. Please try again.'
    if (error.kind === 'network') return error.message
  }
  return 'Your account could not be created. Please try again.'
}

export function RegisterPage() {
  const navigate = useNavigate()
  const [submitError, setSubmitError] = useState<string | null>(null)
  const form = useForm<RegistrationFields>({ resolver: zodResolver(registrationSchema), mode: 'onBlur' })
  const errors = form.formState.errors

  async function onSubmit(values: RegistrationFields) {
    setSubmitError(null)
    try {
      await registerUser({
        email: values.email.trim(),
        password: values.password,
        full_name: values.fullName.trim(),
      })
      form.reset({ fullName: '', email: '', password: '', confirmPassword: '' })
      navigate('/login', { replace: true, state: { reason: 'registration-success' } })
    } catch (error) {
      setSubmitError(registrationErrorMessage(error))
    }
  }

  return (
    <AuthPageLayout eyebrow="Get started" title="Create your account" description="Set up your SecureRAG user account. You will sign in after registration.">
      {submitError && <p className="mb-5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm text-destructive" role="alert">{submitError}</p>}

      <form className="space-y-4" noValidate onSubmit={form.handleSubmit(onSubmit)}>
        <div>
          <label className="mb-1.5 block text-sm font-medium" htmlFor="register-full-name">Full name</label>
          <Input {...form.register('fullName')} id="register-full-name" autoComplete="name" maxLength={200} type="text" aria-invalid={Boolean(errors.fullName)} aria-describedby={errors.fullName ? 'register-full-name-error' : undefined} />
          {errors.fullName && <p className="mt-1.5 text-xs text-destructive" id="register-full-name-error">{errors.fullName.message}</p>}
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium" htmlFor="register-email">Email</label>
          <Input {...form.register('email')} id="register-email" autoComplete="email" autoCapitalize="none" inputMode="email" type="email" aria-invalid={Boolean(errors.email)} aria-describedby={errors.email ? 'register-email-error' : undefined} />
          {errors.email && <p className="mt-1.5 text-xs text-destructive" id="register-email-error">{errors.email.message}</p>}
        </div>
        <div>
          <PasswordField id="register-password" label="Password" autoComplete="new-password" invalid={Boolean(errors.password)} describedBy={errors.password ? 'register-password-error' : undefined} name="password" register={form.register} />
          <p className="mt-1.5 text-xs text-muted-foreground">Use 12–128 characters.</p>
          {errors.password && <p className="mt-1.5 text-xs text-destructive" id="register-password-error">{errors.password.message}</p>}
        </div>
        <div>
          <PasswordField id="register-confirm-password" label="Confirm password" autoComplete="new-password" invalid={Boolean(errors.confirmPassword)} describedBy={errors.confirmPassword ? 'register-confirm-password-error' : undefined} name="confirmPassword" register={form.register} />
          {errors.confirmPassword && <p className="mt-1.5 text-xs text-destructive" id="register-confirm-password-error">{errors.confirmPassword.message}</p>}
        </div>
        <Button className="w-full" disabled={form.formState.isSubmitting} type="submit">
          {form.formState.isSubmitting ? 'Creating account…' : 'Create account'}
        </Button>
      </form>

      <p className="mt-6 text-center text-sm text-muted-foreground">
        Already registered? <Link className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" to="/login">Sign in</Link>
      </p>
    </AuthPageLayout>
  )
}
