import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { useActiveTenant } from './active-tenant-context'
import { createTenant, getTenant, updateTenant } from './api'
import type { Tenant } from './api'
import { tenantQueryKey } from './query-keys'
import { useAuth } from '@/features/auth/auth-context'
import { ApiError } from '@/lib/api/api-error'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ErrorState } from '@/components/shared/error-state'
import { GlobalLoading } from '@/components/shared/global-loading'

const createSchema = z.object({
  name: z.string().trim().min(1, 'Enter a tenant name.').max(200, 'Use 200 characters or fewer.'),
  slug: z.string().trim().min(1, 'Enter a tenant identifier.').max(80, 'Use 80 characters or fewer.'),
  description: z.string().max(4000, 'Use 4,000 characters or fewer.'),
})
const updateSchema = z.object({
  name: z.string().trim().min(1, 'Enter a tenant name.').max(200, 'Use 200 characters or fewer.'),
  description: z.string().max(4000, 'Use 4,000 characters or fewer.'),
})
type CreateFields = z.infer<typeof createSchema>
type UpdateFields = z.infer<typeof updateSchema>

function safeTenantError(error: unknown, action: 'load' | 'create' | 'update'): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return 'That tenant identifier is already in use. Choose another.'
    if (error.status === 403) return 'You do not have permission to make this change.'
    if (error.status === 404) return 'This tenant is unavailable to your account.'
    if (error.status === 422) return 'Review the tenant details and try again.'
  }
  return action === 'load' ? 'Tenant details could not be loaded.' : 'The tenant change could not be completed.'
}

function CreateTenantForm() {
  const { authenticatedRequest } = useAuth()
  const { refreshTenants, selectTenant } = useActiveTenant()
  const form = useForm<CreateFields>({ resolver: zodResolver(createSchema), defaultValues: { name: '', slug: '', description: '' } })
  const create = useMutation({
    mutationFn: (values: CreateFields) => createTenant(authenticatedRequest, {
      name: values.name.trim(), slug: values.slug.trim(), description: values.description.trim() || null,
    }),
    onSuccess: async (created) => {
      form.reset()
      const memberships = await refreshTenants()
      if (memberships?.some((item) => item.tenant.id === created.id)) await selectTenant(created.id)
    },
  })

  return (
    <Card>
      <CardHeader><CardTitle>Create a tenant</CardTitle><CardDescription>Create a workspace and become its OWNER.</CardDescription></CardHeader>
      <CardContent>
        {create.isError && <p className="mb-4 text-sm text-destructive" role="alert">{safeTenantError(create.error, 'create')}</p>}
        {create.isSuccess && <p className="mb-4 text-sm text-emerald-700" role="status">Tenant created and selected.</p>}
        <form className="grid gap-4 sm:grid-cols-2" noValidate onSubmit={form.handleSubmit((values) => create.mutate(values))}>
          <div>
            <label className="mb-1.5 block text-sm font-medium" htmlFor="tenant-create-name">Tenant name</label>
            <Input id="tenant-create-name" maxLength={200} {...form.register('name')} aria-invalid={Boolean(form.formState.errors.name)} />
            {form.formState.errors.name && <p className="mt-1 text-xs text-destructive">{form.formState.errors.name.message}</p>}
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium" htmlFor="tenant-create-slug">Tenant identifier</label>
            <Input id="tenant-create-slug" maxLength={80} autoCapitalize="none" {...form.register('slug')} aria-invalid={Boolean(form.formState.errors.slug)} />
            {form.formState.errors.slug && <p className="mt-1 text-xs text-destructive">{form.formState.errors.slug.message}</p>}
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1.5 block text-sm font-medium" htmlFor="tenant-create-description">Description <span className="font-normal text-muted-foreground">(optional)</span></label>
            <textarea id="tenant-create-description" maxLength={4000} rows={3} className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" {...form.register('description')} />
          </div>
          <div className="sm:col-span-2">
            <Button disabled={create.isPending} type="submit">{create.isPending ? 'Creating…' : 'Create tenant'}</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function TenantDetails({ tenant, tenantId, canManage }: { tenant: Tenant; tenantId: string; canManage: boolean }) {
  const { authenticatedRequest } = useAuth()
  const queryClient = useQueryClient()
  const form = useForm<UpdateFields>({ resolver: zodResolver(updateSchema), values: { name: tenant.name, description: tenant.description ?? '' } })
  const update = useMutation({
    mutationFn: (values: UpdateFields) => {
      const body: { name?: string; description?: string | null } = {}
      if (values.name.trim() !== tenant.name) body.name = values.name.trim()
      const description = values.description.trim() || null
      if (description !== tenant.description) body.description = description
      if (Object.keys(body).length === 0) return Promise.resolve(tenant)
      return updateTenant(authenticatedRequest, tenantId, body)
    },
    onSuccess: (updated) => {
      form.reset({ name: updated.name, description: updated.description ?? '' })
      queryClient.setQueryData(tenantQueryKey(tenantId, 'details'), updated)
    },
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tenant details</CardTitle>
        <CardDescription>Workspace information returned by SecureRAG.</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="mb-6 grid gap-4 sm:grid-cols-2">
          <div><dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Tenant identifier</dt><dd className="mt-1 break-all text-sm">{tenant.slug}</dd></div>
          <div><dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Status</dt><dd className="mt-1 text-sm">{tenant.is_active ? 'Active' : 'Inactive'}</dd></div>
          {tenant.description && <div className="sm:col-span-2"><dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Description</dt><dd className="mt-1 text-sm">{tenant.description}</dd></div>}
        </dl>
        {canManage && <form className="grid gap-4 border-t border-border pt-5 sm:grid-cols-2" noValidate onSubmit={form.handleSubmit((values) => update.mutate(values))}>
          <h3 className="text-sm font-semibold sm:col-span-2">Update tenant</h3>
          {update.isError && <p className="text-sm text-destructive sm:col-span-2" role="alert">{safeTenantError(update.error, 'update')}</p>}
          {update.isSuccess && <p className="text-sm text-emerald-700 sm:col-span-2" role="status">Tenant details saved.</p>}
          <div>
            <label className="mb-1.5 block text-sm font-medium" htmlFor="tenant-update-name">Tenant name</label>
            <Input id="tenant-update-name" maxLength={200} {...form.register('name')} aria-invalid={Boolean(form.formState.errors.name)} />
            {form.formState.errors.name && <p className="mt-1 text-xs text-destructive">{form.formState.errors.name.message}</p>}
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium" htmlFor="tenant-update-description">Description</label>
            <Input id="tenant-update-description" maxLength={4000} {...form.register('description')} />
            {form.formState.errors.description && <p className="mt-1 text-xs text-destructive">{form.formState.errors.description.message}</p>}
          </div>
          <div className="sm:col-span-2"><Button disabled={update.isPending} type="submit">{update.isPending ? 'Saving…' : 'Save tenant details'}</Button></div>
        </form>}
        {!canManage && <p className="border-t border-border pt-5 text-sm text-muted-foreground">Tenant settings are managed by an OWNER or ADMIN.</p>}
      </CardContent>
    </Card>
  )
}

export function TenantWorkspacePage() {
  const auth = useAuth()
  const { activeTenant, tenants, isTenantsLoading, tenantsError, unavailableTenantNotice, isSwitchingTenant, refreshTenants, clearUnavailableTenant } = useActiveTenant()
  const tenantId = activeTenant?.tenant.id
  const tenantQuery = useQuery({
    queryKey: tenantId ? tenantQueryKey(tenantId, 'details') : ['tenant', 'none', 'details'],
    queryFn: ({ signal }) => getTenant(auth.authenticatedRequest, tenantId!, signal),
    enabled: Boolean(tenantId),
    staleTime: 15_000,
  })
  useEffect(() => {
    if (tenantId && tenantQuery.error instanceof ApiError && [403, 404].includes(tenantQuery.error.status ?? 0)) clearUnavailableTenant(tenantId)
  }, [clearUnavailableTenant, tenantId, tenantQuery.error])

  if (isTenantsLoading || isSwitchingTenant) return <GlobalLoading label={isSwitchingTenant ? 'Switching tenant workspace' : 'Loading tenant workspaces'} />
  return (
    <div className="space-y-8">
      <header><p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Workspace</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Tenant workspace</h1><p className="mt-2 text-sm text-muted-foreground">Manage your company workspace and its membership.</p></header>
      {tenantsError && <ErrorState title="Tenant list unavailable" message="Your tenant workspaces could not be loaded. Refresh to try again." />}
      {unavailableTenantNotice && <ErrorState title="Tenant unavailable" message="This tenant is no longer available to your account. Choose another tenant or refresh the list." />}
      {!tenantsError && tenants.length === 0 && <div className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">You do not belong to an active tenant yet. Create one below or ask an existing tenant OWNER to add your account.</div>}
      {activeTenant && <>
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card px-4 py-3">
          <span className="text-sm font-medium">Your role</span><span className="rounded-full bg-accent px-3 py-1 text-xs font-semibold">{activeTenant.role}</span>
        </div>
        {tenantQuery.isLoading && <GlobalLoading label="Loading tenant details" />}
        {tenantQuery.isError && <ErrorState title="Tenant unavailable" message={safeTenantError(tenantQuery.error, 'load')} />}
        {tenantQuery.data && <TenantDetails key={tenantQuery.data.id} tenant={tenantQuery.data} tenantId={tenantQuery.data.id} canManage={['OWNER', 'ADMIN'].includes(activeTenant.role)} />}
      </>}
      {!activeTenant && tenants.length > 0 && <p className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">Select a tenant from the workspace selector to view its details.</p>}
      <CreateTenantForm />
      {tenantsError && <Button onClick={() => void refreshTenants()} variant="outline">Retry tenant list</Button>}
    </div>
  )
}
