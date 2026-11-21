import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { Link } from 'react-router-dom'
import { useActiveTenant } from '@/features/tenants/active-tenant-context'
import { addMembership, listMemberships, updateMembership } from '@/features/tenants/api'
import type { Membership, TenantRole } from '@/features/tenants/api'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { useAuth } from '@/features/auth/auth-context'
import { ApiError } from '@/lib/api/api-error'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorState } from '@/components/shared/error-state'
import { GlobalLoading } from '@/components/shared/global-loading'

const roles: TenantRole[] = ['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE']
const addMemberSchema = z.object({ email: z.email('Enter a valid email address.'), role: z.enum(roles) })
type AddMemberFields = z.infer<typeof addMemberSchema>

function safeMembershipError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return 'Your role cannot make this membership change.'
    if (error.status === 404) return 'The user or tenant is unavailable. Check the email and try again.'
    if (error.status === 409) return 'This change conflicts with an existing membership or tenant ownership rule.'
    if (error.status === 422) return 'Review the email, role, or membership values and try again.'
  }
  return 'The membership change could not be completed.'
}

function AddMemberForm({ tenantId, actorRole, onAdded }: { tenantId: string; actorRole: TenantRole; onAdded: () => void }) {
  const { authenticatedRequest } = useAuth()
  const form = useForm<AddMemberFields>({ resolver: zodResolver(addMemberSchema), defaultValues: { email: '', role: 'EMPLOYEE' } })
  const add = useMutation({
    mutationFn: (values: AddMemberFields) => addMembership(authenticatedRequest, tenantId, { email: values.email.trim(), role: values.role }),
    onSuccess: () => { form.reset({ email: '', role: 'EMPLOYEE' }); onAdded() },
  })
  const availableRoles = actorRole === 'OWNER' ? roles : roles.filter((role) => !['OWNER', 'ADMIN'].includes(role))

  return <Card>
    <CardHeader><CardTitle>Add an existing user</CardTitle><CardDescription>Membership requires an account that already exists in SecureRAG. No invitation email is sent.</CardDescription></CardHeader>
    <CardContent>
      {add.isError && <p className="mb-4 text-sm text-destructive" role="alert">{safeMembershipError(add.error)}</p>}
      {add.isSuccess && <p className="mb-4 text-sm text-emerald-700" role="status">Membership added.</p>}
      <form className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_14rem_auto] sm:items-end" noValidate onSubmit={form.handleSubmit((values) => add.mutate(values))}>
        <div>
          <label className="mb-1.5 block text-sm font-medium" htmlFor="member-email">Existing user email</label>
          <input id="member-email" type="email" autoComplete="email" autoCapitalize="none" className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" {...form.register('email')} aria-invalid={Boolean(form.formState.errors.email)} />
          {form.formState.errors.email && <p className="mt-1 text-xs text-destructive">{form.formState.errors.email.message}</p>}
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium" htmlFor="member-role">Tenant role</label>
          <select id="member-role" className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" {...form.register('role')}>
            {availableRoles.map((role) => <option key={role} value={role}>{role}</option>)}
          </select>
        </div>
        <Button disabled={add.isPending} type="submit">{add.isPending ? 'Adding…' : 'Add member'}</Button>
      </form>
    </CardContent>
  </Card>
}

function MembershipRow({ membership, tenantId, actorId, actorRole, onUpdated }: {
  membership: Membership; tenantId: string; actorId: string; actorRole: TenantRole; onUpdated: () => void
}) {
  const { authenticatedRequest } = useAuth()
  const [role, setRole] = useState(membership.role)
  const [isActive, setIsActive] = useState(membership.is_active)
  const update = useMutation({
    mutationFn: () => {
      const patch: { role?: TenantRole; is_active?: boolean } = {}
      if (role !== membership.role) patch.role = role
      if (isActive !== membership.is_active) patch.is_active = isActive
      return updateMembership(authenticatedRequest, tenantId, membership.id, patch)
    },
    onSuccess: (updated) => { setRole(updated.role); setIsActive(updated.is_active); onUpdated() },
  })
  const isPrivilegedTarget = ['OWNER', 'ADMIN'].includes(membership.role)
  const canEdit = actorRole === 'OWNER' || (actorRole === 'ADMIN' && !isPrivilegedTarget)
  const selfDeactivation = membership.user_id === actorId && membership.is_active && !isActive
  const changed = role !== membership.role || isActive !== membership.is_active

  function submit() {
    if (selfDeactivation) return
    const changes: string[] = []
    if (role !== membership.role) {
      const privilegeNote = ['OWNER', 'ADMIN'].includes(role) ? ' This grants elevated tenant privileges.' : ''
      changes.push(`change the role to ${role}.${privilegeNote}`)
    }
    if (isActive !== membership.is_active) {
      changes.push(isActive ? 'activate the tenant membership.' : 'deactivate the tenant membership.')
    }
    if (changes.length > 0 && !window.confirm(`Confirm changes for ${membership.user.full_name}: ${changes.join(' ')}`)) return
    update.mutate()
  }

  return <tr className="border-t border-border align-top">
    <td className="px-3 py-4"><div className="font-medium">{membership.user.full_name}</div><div className="mt-1 break-all text-xs text-muted-foreground">{membership.user.email}</div>{!membership.user.is_active && <span className="mt-2 inline-block text-xs text-amber-700">Account inactive</span>}</td>
    <td className="px-3 py-4">
      <label className="sr-only" htmlFor={`membership-role-${membership.id}`}>Role for {membership.user.full_name}</label>
      <select id={`membership-role-${membership.id}`} disabled={!canEdit} value={role} onChange={(event) => setRole(event.target.value as TenantRole)} className="h-9 rounded-md border border-input bg-background px-2 text-xs disabled:opacity-70">
        {(actorRole === 'OWNER' ? roles : roles.filter((item) => !['OWNER', 'ADMIN'].includes(item))).map((item) => <option key={item} value={item}>{item}</option>)}
        {!roles.includes(role) && <option value={role}>{role}</option>}
      </select>
      {actorRole === 'ADMIN' && isPrivilegedTarget && <span className="mt-1 block max-w-36 text-xs text-muted-foreground">Only an OWNER can manage this membership.</span>}
    </td>
    <td className="px-3 py-4">
      <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={isActive} disabled={!canEdit || (membership.user_id === actorId && membership.is_active)} onChange={(event) => setIsActive(event.target.checked)} />{isActive ? 'Active' : 'Inactive'}</label>
      {membership.user_id === actorId && membership.is_active && <span className="mt-1 block text-xs text-muted-foreground">You cannot deactivate your own membership.</span>}
    </td>
    <td className="px-3 py-4">
      {update.isError && <p className="mb-2 max-w-44 text-xs text-destructive" role="alert">{safeMembershipError(update.error)}</p>}
      {canEdit && <Button size="sm" variant="outline" disabled={!changed || update.isPending || selfDeactivation} onClick={submit}>{update.isPending ? 'Saving…' : 'Save'}</Button>}
      {!canEdit && <span className="text-xs text-muted-foreground">View only</span>}
    </td>
  </tr>
}

export function MembershipsPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const { activeTenant, isSwitchingTenant, clearUnavailableTenant } = useActiveTenant()
  const tenantId = activeTenant?.tenant.id
  const role = activeTenant?.role
  const isAdmin = role === 'OWNER' || role === 'ADMIN'
  const query = useQuery({
    queryKey: tenantId ? tenantQueryKey(tenantId, 'memberships') : ['tenant', 'none', 'memberships'],
    queryFn: ({ signal }) => listMemberships(auth.authenticatedRequest, tenantId!, signal),
    enabled: Boolean(tenantId && isAdmin),
  })
  useEffect(() => {
    if (tenantId && query.error instanceof ApiError && [403, 404].includes(query.error.status ?? 0)) clearUnavailableTenant(tenantId)
  }, [clearUnavailableTenant, query.error, tenantId])
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: tenantId ? tenantQueryKey(tenantId, 'memberships') : [] }) }

  if (isSwitchingTenant) return <GlobalLoading label="Switching tenant workspace" />
  if (!activeTenant) return <ErrorState title="Select a tenant first" message="Choose an active tenant workspace from the selector to manage its memberships." />
  if (!isAdmin) return <ErrorState title="Membership management unavailable" message="Only tenant OWNERs and ADMINs can view or manage memberships." />

  return <div className="space-y-8">
    <header><Link className="text-sm font-medium text-primary hover:underline" to="/">← Tenant workspace</Link><p className="mt-5 text-xs font-semibold uppercase tracking-[0.16em] text-primary">{activeTenant.tenant.name}</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Memberships</h1><p className="mt-2 text-sm text-muted-foreground">Manage access to this tenant. The server enforces role and ownership rules.</p></header>
    <AddMemberForm tenantId={activeTenant.tenant.id} actorRole={activeTenant.role} onAdded={refresh} />
    <Card>
      <CardHeader><CardTitle>Tenant members</CardTitle><CardDescription>Membership role is tenant-specific. Global account flags do not grant tenant privileges.</CardDescription></CardHeader>
      <CardContent>
        {query.isLoading && <GlobalLoading label="Loading memberships" />}
        {query.isError && <ErrorState title="Memberships unavailable" message={safeMembershipError(query.error)} />}
        {query.data?.length === 0 && <p className="text-sm text-muted-foreground">No memberships were returned for this tenant.</p>}
        {query.data && query.data.length > 0 && <div className="overflow-x-auto"><table className="w-full min-w-[680px] text-left text-sm"><thead><tr className="text-xs uppercase tracking-wide text-muted-foreground"><th className="px-3 pb-3">User</th><th className="px-3 pb-3">Role</th><th className="px-3 pb-3">Status</th><th className="px-3 pb-3">Action</th></tr></thead><tbody>{query.data.map((membership) => <MembershipRow key={membership.id} membership={membership} tenantId={activeTenant.tenant.id} actorId={auth.user!.id} actorRole={activeTenant.role} onUpdated={refresh} />)}</tbody></table></div>}
      </CardContent>
    </Card>
  </div>
}
