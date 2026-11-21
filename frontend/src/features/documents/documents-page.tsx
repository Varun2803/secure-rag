import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorState } from '@/components/shared/error-state'
import { GlobalLoading } from '@/components/shared/global-loading'
import { useAuth } from '@/features/auth/auth-context'
import { useActiveTenant } from '@/features/tenants/active-tenant-context'
import type { TenantRole } from '@/features/tenants/api'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { ApiError } from '@/lib/api/api-error'
import { getDocument, getDocumentStatus, listDocuments, uploadDocument } from './api'
import type { DocumentRecord } from './api'
import { PermissionEditor } from '@/features/permissions/permission-editor'

const PAGE_SIZE = 50
const MAX_FILE_BYTES = 20 * 1024 * 1024
const uploadRoles: TenantRole[] = ['OWNER', 'ADMIN', 'HR']
const classificationLabels: Record<number, string> = { 0: 'Public', 1: 'Internal', 2: 'Confidential', 3: 'Restricted' }
const statusVariants = { PENDING: 'outline', PROCESSING: 'warning', COMPLETED: 'success', FAILED: 'destructive' } as const

function safeDocumentError(error: unknown, kind: 'list' | 'details' | 'upload'): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401: return 'Your sign-in is required or has expired.'
      case 403: return kind === 'upload' ? 'Your tenant role does not allow document uploads.' : 'You do not have permission to access documents in this tenant.'
      case 404: return 'This tenant or document is unavailable.'
      case 409: return 'This document already exists in this tenant.'
      case 413: return 'The file exceeds the 20 MiB upload limit.'
      case 415: return 'Only PDF files are supported.'
      case 422: return 'The PDF or submitted document metadata is invalid. Check that the PDF is readable and within the 500-page limit.'
      case 503: return 'Document processing is temporarily unavailable. Please try again later.'
    }
  }
  return kind === 'list' ? 'Documents could not be loaded.' : kind === 'details' ? 'Document details could not be loaded.' : 'The PDF could not be uploaded and processed.'
}

function displayProjection(document: DocumentRecord) {
  return {
    id: document.id,
    originalFilename: document.original_filename,
    fileSize: document.file_size,
    uploadedAt: document.created_at,
    status: document.status,
    pageCount: document.page_count,
    chunkCount: document.chunk_count,
    classification: classificationLabels[document.classification] ?? 'Unknown',
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function statusVariant(status: DocumentRecord['status']) {
  return statusVariants[status]
}

function failureMessage(code: string | null): string {
  const safeMessages: Record<string, string> = {
    PDF_INVALID: 'The PDF could not be validated or did not contain extractable text.',
    EMBEDDING_FAILED: 'Text processing is temporarily unavailable.',
    VECTOR_STORAGE_FAILED: 'Vector storage is temporarily unavailable.',
    PROCESSING_FAILED: 'Document processing could not be completed.',
  }
  return code ? safeMessages[code] ?? 'Document processing failed. Contact an administrator if the issue continues.' : 'No failure details are available.'
}

function UploadPanel({ tenantId, role, onUploaded }: { tenantId: string; role: TenantRole; onUploaded: () => void }) {
  const { authenticatedRequest } = useAuth()
  const [file, setFile] = useState<File | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const upload = useMutation({
    mutationFn: (selectedFile: File) => uploadDocument(authenticatedRequest, tenantId, selectedFile),
    onSuccess: (uploadedDocument) => {
      setFile(null)
      setValidationError(null)
      setSuccessMessage(`${uploadedDocument.original_filename} was uploaded and processed.`)
      const input = document.getElementById('document-pdf-file')
      if (input instanceof HTMLInputElement) input.value = ''
      onUploaded()
    },
  })

  if (!uploadRoles.includes(role)) return null

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSuccessMessage(null)
    if (!file) {
      setValidationError('Choose a PDF file to upload.')
      return
    }
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setValidationError('Choose a file with a .pdf extension. The server will validate the actual file content.')
      return
    }
    if (file.size <= 0) {
      setValidationError('The selected file is empty.')
      return
    }
    if (file.size > MAX_FILE_BYTES) {
      setValidationError('The file exceeds the 20 MiB upload limit.')
      return
    }
    setValidationError(null)
    upload.mutate(file)
  }

  return <Card>
    <CardHeader>
      <CardTitle>Upload a PDF</CardTitle>
      <CardDescription>PDF files only · Maximum 20 MiB and 500 pages. The server verifies the actual file format.</CardDescription>
    </CardHeader>
    <CardContent>
      <p className="mb-4 rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground">New uploads start private with no document-content grants. Classification labels do not grant access, and uploader status does not grant access. An OWNER or ADMIN can set an explicit document ACL.</p>
      {validationError && <p role="alert" className="mb-3 text-sm text-destructive">{validationError}</p>}
      {upload.isError && <p role="alert" className="mb-3 text-sm text-destructive">{safeDocumentError(upload.error, 'upload')}</p>}
      {successMessage && <p role="status" className="mb-3 text-sm text-emerald-700 dark:text-emerald-300">{successMessage}</p>}
      <form className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={submit}>
        <div className="min-w-0 flex-1">
          <label htmlFor="document-pdf-file" className="mb-1.5 block text-sm font-medium">PDF file</label>
          <input id="document-pdf-file" type="file" accept=".pdf,application/pdf" disabled={upload.isPending} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setValidationError(null); setSuccessMessage(null); upload.reset() }} className="block min-h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-secondary file:px-2 file:py-1 file:text-xs" />
          {file && <p className="mt-1 text-xs text-muted-foreground">{file.name} · {formatFileSize(file.size)}</p>}
        </div>
        <Button disabled={upload.isPending} type="submit">{upload.isPending ? 'Uploading and processing…' : 'Upload PDF'}</Button>
      </form>
      <p className="mt-3 text-xs text-muted-foreground">Processing runs synchronously. This request returns when ingestion completes; no background progress is reported.</p>
    </CardContent>
  </Card>
}

function DocumentDetails({ tenantId, documentId }: { tenantId: string; documentId: string }) {
  const { authenticatedRequest } = useAuth()
  const details = useQuery({
    queryKey: tenantQueryKey(tenantId, 'documents', 'detail', documentId),
    queryFn: ({ signal }) => getDocument(authenticatedRequest, tenantId, documentId, signal),
  })
  const status = useQuery({
    queryKey: tenantQueryKey(tenantId, 'documents', 'status', documentId),
    queryFn: ({ signal }) => getDocumentStatus(authenticatedRequest, tenantId, documentId, signal),
    enabled: details.isSuccess,
  })

  if (details.isLoading) return <GlobalLoading label="Loading document details" />
  if (details.isError) return <ErrorState title="Document details unavailable" message={safeDocumentError(details.error, 'details')} />
  if (!details.data) return <ErrorState title="Document details unavailable" message="Document details could not be loaded." />
  const document = displayProjection(details.data)

  return <Card role="region" aria-label="Document details">
    <CardHeader className="flex flex-row items-start justify-between gap-3">
      <div><CardTitle>Document details</CardTitle><CardDescription>{document.originalFilename}</CardDescription></div>
      <Button variant="outline" size="sm" onClick={() => void status.refetch()} disabled={status.isFetching}>Refresh status</Button>
    </CardHeader>
    <CardContent>
      <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
        <div><dt className="text-xs text-muted-foreground">Processing status</dt><dd className="mt-1">{status.data ? <Badge variant={statusVariants[status.data.status]}>{status.data.status}</Badge> : status.isLoading ? 'Loading…' : 'Unavailable'}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Pages</dt><dd className="mt-1">{status.data?.page_count ?? document.pageCount}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Chunks</dt><dd className="mt-1">{status.data?.chunk_count ?? document.chunkCount}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Classification</dt><dd className="mt-1">{document.classification}</dd></div>
        <div><dt className="text-xs text-muted-foreground">File size</dt><dd className="mt-1">{formatFileSize(document.fileSize)}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Uploaded</dt><dd className="mt-1">{formatDate(document.uploadedAt)}</dd></div>
      </dl>
      {status.isError && <p role="alert" className="mt-4 text-sm text-destructive">{safeDocumentError(status.error, 'details')}</p>}
      {status.data?.status === 'FAILED' && <p role="status" className="mt-4 rounded-lg border border-destructive/30 p-3 text-sm">{failureMessage(status.data.failure_detail)}</p>}
      <p className="mt-4 text-xs text-muted-foreground">Status is persisted by the server. Refresh manually to check for a later status; processing is not a background progress stream.</p>
    </CardContent>
  </Card>
}

export function DocumentsPage() {
  const { authenticatedRequest } = useAuth()
  const queryClient = useQueryClient()
  const { activeTenant, isSwitchingTenant } = useActiveTenant()
  const tenantId = activeTenant?.tenant.id
  const role = activeTenant?.role
  const [pagination, setPagination] = useState<{ tenantId: string | undefined; offset: number }>({ tenantId, offset: 0 })
  const [selection, setSelection] = useState<{ tenantId: string | undefined; documentId: string | null }>({ tenantId, documentId: null })
  const [permissionSelection, setPermissionSelection] = useState<{ tenantId: string | undefined; documentId: string | null }>({ tenantId, documentId: null })
  const offset = pagination.tenantId === tenantId ? pagination.offset : 0
  const selectedDocumentId = selection.tenantId === tenantId ? selection.documentId : null
  const selectedPermissionDocumentId = permissionSelection.tenantId === tenantId ? permissionSelection.documentId : null

  const documents = useQuery({
    queryKey: tenantId ? tenantQueryKey(tenantId, 'documents', 'list', { limit: PAGE_SIZE, offset }) : ['tenant', 'none', 'documents'],
    queryFn: ({ signal }) => listDocuments(authenticatedRequest, tenantId!, PAGE_SIZE, offset, signal),
    enabled: Boolean(tenantId),
  })
  const invalidateDocumentLists = () => {
    if (tenantId) void queryClient.invalidateQueries({ queryKey: tenantQueryKey(tenantId, 'documents', 'list') })
  }

  if (isSwitchingTenant) return <GlobalLoading label="Switching tenant workspace" />
  if (!activeTenant) return <div className="space-y-6"><header><h1 className="text-3xl font-semibold tracking-tight">Documents</h1><p className="mt-2 text-sm text-muted-foreground">Select an active tenant workspace to view its documents.</p></header><Link to="/" className="font-medium text-primary hover:underline">Go to tenant workspace</Link></div>

  const total = documents.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  return <div className="space-y-7">
    <header><p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">{activeTenant.tenant.name}</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Documents</h1><p className="mt-2 text-sm text-muted-foreground">Browse this tenant’s uploaded PDFs and persisted ingestion status.</p></header>
    {role && <UploadPanel tenantId={activeTenant.tenant.id} role={role} onUploaded={() => { setPagination({ tenantId, offset: 0 }); invalidateDocumentLists() }} />}
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div><CardTitle>Tenant documents</CardTitle><CardDescription>{documents.data ? `${total} document${total === 1 ? '' : 's'} total` : 'Documents are visible only within this tenant.'}</CardDescription></div>
        <Button variant="outline" size="sm" disabled={documents.isFetching} onClick={() => void documents.refetch()}>{documents.isFetching ? 'Refreshing…' : 'Refresh list'}</Button>
      </CardHeader>
      <CardContent>
        {documents.isLoading && <GlobalLoading label="Loading documents" />}
        {documents.isError && <div className="space-y-3"><ErrorState title="Documents unavailable" message={safeDocumentError(documents.error, 'list')} /><Button variant="outline" onClick={() => void documents.refetch()}>Retry</Button></div>}
        {documents.data?.items.length === 0 && <p className="rounded-lg border border-dashed border-border p-5 text-sm text-muted-foreground">No documents have been uploaded to this tenant yet.</p>}
        {documents.data && documents.data.items.length > 0 && <ul className="divide-y divide-border">
          {documents.data.items.map((record) => {
            const item = displayProjection(record)
            return <li key={item.id} className="grid gap-3 py-4 first:pt-0 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <div className="min-w-0"><h3 className="break-words font-medium">{item.originalFilename}</h3><div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"><span>{formatFileSize(item.fileSize)}</span><span>{formatDate(item.uploadedAt)}</span><span>{item.pageCount} pages</span><span>{item.chunkCount} chunks</span><span>{item.classification}</span></div></div>
              <div className="flex flex-wrap items-center gap-2"><Badge variant={statusVariant(item.status)}>{item.status}</Badge><Button size="sm" variant="outline" aria-expanded={selectedDocumentId === item.id} onClick={() => setSelection({ tenantId, documentId: selectedDocumentId === item.id ? null : item.id })}>{selectedDocumentId === item.id ? 'Hide details' : 'View details'}</Button>{role && ['OWNER', 'ADMIN'].includes(role) && <Button size="sm" variant="outline" aria-expanded={selectedPermissionDocumentId === item.id} onClick={() => setPermissionSelection({ tenantId, documentId: selectedPermissionDocumentId === item.id ? null : item.id })}>{selectedPermissionDocumentId === item.id ? 'Hide permissions' : 'Manage permissions'}</Button>}</div>
            </li>
          })}
        </ul>}
        {selectedDocumentId && documents.data?.items.some((item) => item.id === selectedDocumentId) && <div className="mt-5"><DocumentDetails tenantId={activeTenant.tenant.id} documentId={selectedDocumentId} /></div>}
        {selectedPermissionDocumentId && role && ['OWNER', 'ADMIN'].includes(role) && documents.data?.items.some((item) => item.id === selectedPermissionDocumentId) && <div className="mt-5"><PermissionEditor key={`${activeTenant.tenant.id}:${selectedPermissionDocumentId}`} tenantId={activeTenant.tenant.id} documentId={selectedPermissionDocumentId} tenantRole={role} /></div>}
        {documents.data && total > PAGE_SIZE && <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"><p className="text-sm text-muted-foreground">Page {currentPage} of {pageCount}</p><div className="flex gap-2"><Button variant="outline" size="sm" disabled={offset === 0 || documents.isFetching} onClick={() => setPagination({ tenantId, offset: Math.max(0, offset - PAGE_SIZE) })}>Previous</Button><Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= total || documents.isFetching} onClick={() => setPagination({ tenantId, offset: offset + PAGE_SIZE })}>Next</Button></div></div>}
      </CardContent>
    </Card>
  </div>
}
