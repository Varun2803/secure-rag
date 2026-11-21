import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { ErrorState } from '@/components/shared/error-state'
import { GlobalLoading } from '@/components/shared/global-loading'
import { useAuth } from '@/features/auth/auth-context'
import { listMemberships } from '@/features/tenants/api'
import type { TenantRole } from '@/features/tenants/api'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { ApiError } from '@/lib/api/api-error'
import { getDocumentPermissions, isTenantPermissionManager, permissionReplacementSchema, replaceDocumentPermissions } from './api'
import type { DocumentPermissions, PermissionReplacement } from './api'

const roles: TenantRole[] = ['OWNER', 'ADMIN', 'HR', 'MANAGER', 'EMPLOYEE']
const classifications = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED'] as const
type FormFields = z.infer<typeof permissionReplacementSchema>

function safePermissionError(error: unknown, saving = false): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Your sign-in is required or has expired.'
    if (error.status === 403) return 'Only tenant OWNERs and ADMINs can manage document permissions.'
    if (error.status === 404) return 'This tenant or document is unavailable.'
    if (error.status === 409) return 'Permissions cannot be changed until document processing is complete.'
    if (error.status === 422) return 'The selected roles or users are invalid. Choose active members of this tenant.'
    if (error.status === 503) return saving ? 'The permission change could not be saved. Check synchronization status before retrying.' : 'Document permissions are temporarily unavailable.'
  }
  return saving ? 'The permission change could not be saved.' : 'Document permissions could not be loaded.'
}

function readableSyncStatus(status: DocumentPermissions['synchronization_status']): { label: string; message: string; variant: 'success' | 'warning' } {
  if (status === 'COMPLETED') return { label: 'Synchronized', message: 'The backend confirms Qdrant synchronization completed.', variant: 'success' }
  return { label: status === 'PENDING' ? 'Synchronization pending' : 'Synchronization processing', message: 'The PostgreSQL permission policy is saved. Vector synchronization is not yet confirmed; retrieval remains authorized against PostgreSQL.', variant: 'warning' }
}

function toFormValues(value: DocumentPermissions): FormFields {
  return { classification: value.classification, is_public: value.is_public, allowed_roles: value.allowed_roles, allowed_users: value.allowed_users }
}

export function PermissionEditor({ tenantId, documentId, tenantRole }: { tenantId: string; documentId: string; tenantRole: TenantRole }) {
  const { authenticatedRequest } = useAuth()
  const queryClient = useQueryClient()
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  const [pendingValues, setPendingValues] = useState<FormFields | null>(null)
  const allowed = isTenantPermissionManager(tenantRole)
  const permissionKey = tenantQueryKey(tenantId, 'documents', 'permissions', documentId)
  const permissionQuery = useQuery({
    queryKey: permissionKey,
    queryFn: ({ signal }) => getDocumentPermissions(authenticatedRequest, tenantId, documentId, signal),
    enabled: allowed,
  })
  const membershipsQuery = useQuery({
    queryKey: tenantQueryKey(tenantId, 'memberships'),
    queryFn: ({ signal }) => listMemberships(authenticatedRequest, tenantId, signal),
    enabled: allowed,
  })
  const form = useForm<FormFields>({ resolver: zodResolver(permissionReplacementSchema), defaultValues: { classification: 'INTERNAL', is_public: false, allowed_roles: [], allowed_users: [] } })
  useEffect(() => {
    if (permissionQuery.data) {
      const values = toFormValues(permissionQuery.data)
      form.reset(values)
    }
  }, [form, permissionQuery.data])

  const save = useMutation({
    mutationFn: (values: PermissionReplacement) => replaceDocumentPermissions(authenticatedRequest, tenantId, documentId, values),
    onSuccess: (result) => {
      queryClient.setQueryData(permissionKey, result)
      const values = toFormValues(result)
      form.reset(values)
      setConfirmationOpen(false)
      setPendingValues(null)
    },
    onError: () => {
      setConfirmationOpen(false)
      setPendingValues(null)
    },
  })

  if (!allowed) return null
  if (permissionQuery.isLoading) return <Card><CardContent className="pt-6"><GlobalLoading label="Loading document permissions" /></CardContent></Card>
  if (permissionQuery.isError || !permissionQuery.data) return <Card><CardContent className="pt-6"><ErrorState title="Permissions unavailable" message={safePermissionError(permissionQuery.error)} /><Button className="mt-3" variant="outline" onClick={() => void permissionQuery.refetch()}>Retry</Button></CardContent></Card>

  const current = permissionQuery.data
  const currentFormValues = form.getValues()
  const sync = readableSyncStatus(current.synchronization_status)
  const activeUsers = (membershipsQuery.data ?? []).filter((membership) => membership.is_active && membership.user.is_active)

  function requestConfirmation(values: FormFields) {
    setPendingValues(values)
    setConfirmationOpen(true)
  }

  function confirmReplacement() {
    if (pendingValues) save.mutate(pendingValues)
  }

  return <>
    <Card role="region" aria-label={`Permissions for document ${documentId}`}>
      <CardHeader>
        <CardTitle>Document permissions</CardTitle>
        <CardDescription>Replace the complete ACL for this document. The server validates every selected user against active membership in this tenant.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">Vector synchronization:</span><Badge variant={sync.variant}>{sync.label}</Badge></div>
        <p role="status" className="text-sm text-muted-foreground">{sync.message}</p>
        {save.isError && <p role="alert" className="text-sm text-destructive">{safePermissionError(save.error, true)}</p>}
        {save.isSuccess && <p role="status" className="text-sm text-emerald-700 dark:text-emerald-300">The complete ACL was saved. The values below reflect the backend response.</p>}
        {membershipsQuery.isError && <p role="alert" className="text-sm text-destructive">Tenant members could not be loaded. User grants cannot be edited until the member list is available.</p>}
        <div className="rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
          Public access is scoped to this tenant. A private ACL with no role or user grants denies content access. Classification is a label only. Uploader, OWNER, and ADMIN status do not grant document-content access automatically.
        </div>
        <form className="space-y-5" noValidate onSubmit={form.handleSubmit(requestConfirmation)}>
          <div>
            <label htmlFor={`permission-classification-${documentId}`} className="mb-1.5 block text-sm font-medium">Classification</label>
            <select id={`permission-classification-${documentId}`} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" {...form.register('classification')}>
              {classifications.map((classification) => <option key={classification} value={classification}>{classification}</option>)}
            </select>
            {form.formState.errors.classification && <p className="mt-1 text-xs text-destructive">{form.formState.errors.classification.message}</p>}
          </div>
          <fieldset className="space-y-2">
            <legend className="mb-2 text-sm font-medium">Visibility</legend>
            <label className="flex items-start gap-2 text-sm"><input name={`permission-visibility-${documentId}`} type="radio" value="public" checked={currentFormValues.is_public} onChange={() => form.setValue('is_public', true, { shouldDirty: true })} /><span><span className="font-medium">Public within this tenant</span><span className="block text-xs text-muted-foreground">Tenant members may access the document when backend status and tenant checks pass.</span></span></label>
            <label className="flex items-start gap-2 text-sm"><input name={`permission-visibility-${documentId}`} type="radio" value="private" checked={!currentFormValues.is_public} onChange={() => form.setValue('is_public', false, { shouldDirty: true })} /><span><span className="font-medium">Private</span><span className="block text-xs text-muted-foreground">Only explicitly granted roles or users can access document content.</span></span></label>
          </fieldset>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Allowed tenant roles</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {roles.map((role) => <label key={role} className="flex items-center gap-2 text-sm"><input type="checkbox" value={role} checked={currentFormValues.allowed_roles.includes(role)} onChange={(event) => {
                const selected = event.target.checked ? [...currentFormValues.allowed_roles, role] : currentFormValues.allowed_roles.filter((item) => item !== role)
                form.setValue('allowed_roles', selected, { shouldDirty: true, shouldValidate: true })
              }} />{role}</label>)}
            </div>
            {form.formState.errors.allowed_roles && <p className="mt-1 text-xs text-destructive">{form.formState.errors.allowed_roles.message}</p>}
          </fieldset>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Allowed tenant users</legend>
            {membershipsQuery.isLoading && <p className="text-sm text-muted-foreground">Loading active tenant members…</p>}
            {membershipsQuery.data && activeUsers.length === 0 && <p className="text-sm text-muted-foreground">No active tenant members are available to select.</p>}
            <div className="grid max-h-48 gap-2 overflow-y-auto rounded-md border border-border p-3 sm:grid-cols-2">
              {activeUsers.map((membership) => <label key={membership.user_id} className="flex min-w-0 items-start gap-2 text-sm"><input type="checkbox" value={membership.user_id} checked={currentFormValues.allowed_users.includes(membership.user_id)} disabled={membershipsQuery.isLoading || membershipsQuery.isError} onChange={(event) => {
                const selected = event.target.checked ? [...currentFormValues.allowed_users, membership.user_id] : currentFormValues.allowed_users.filter((item) => item !== membership.user_id)
                form.setValue('allowed_users', selected, { shouldDirty: true, shouldValidate: true })
              }} /><span className="min-w-0"><span className="block truncate">{membership.user.full_name}</span><span className="block truncate text-xs text-muted-foreground">{membership.user.email}</span></span></label>)}
            </div>
            {form.formState.errors.allowed_users && <p className="mt-1 text-xs text-destructive">{form.formState.errors.allowed_users.message}</p>}
          </fieldset>
          <Button type="submit" disabled={save.isPending || membershipsQuery.isLoading || membershipsQuery.isError || !form.formState.isDirty}>{save.isPending ? 'Saving permissions…' : 'Review complete ACL'}</Button>
        </form>
      </CardContent>
    </Card>
    <Dialog open={confirmationOpen} onOpenChange={(open) => { if (!save.isPending) setConfirmationOpen(open) }}>
      <DialogContent aria-describedby={`permission-confirmation-description-${documentId}`}>
        <DialogTitle>Replace document permissions?</DialogTitle>
        <DialogDescription id={`permission-confirmation-description-${documentId}`}>
          This replaces the entire current ACL. The document will be {pendingValues?.is_public ? 'public within this tenant' : 'private'}, classified as {pendingValues?.classification}, with {pendingValues?.allowed_roles.length ?? 0} role grants and {pendingValues?.allowed_users.length ?? 0} user grants. Only the backend can authorize access; your administrator role does not grant document-content access.
        </DialogDescription>
        <div className="flex justify-end gap-2"><Button variant="outline" disabled={save.isPending} onClick={() => { setConfirmationOpen(false); setPendingValues(null) }}>Cancel</Button><Button disabled={save.isPending} onClick={confirmReplacement}>{save.isPending ? 'Saving…' : 'Confirm replacement'}</Button></div>
      </DialogContent>
    </Dialog>
  </>
}
