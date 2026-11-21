import { useQuery } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link } from 'react-router-dom'
import { ErrorState } from '@/components/shared/error-state'
import { GlobalLoading } from '@/components/shared/global-loading'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuth } from '@/features/auth/auth-context'
import { useActiveTenant } from '@/features/tenants/active-tenant-context'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { GenerationPanel } from '@/features/generation/generation-panel'
import { safeKnowledgeError } from './errors'
import { knowledgeRequestSchema, searchDocuments } from './api'
import type { KnowledgeRequest, RetrievalResponse } from './api'

let retrievalRequestSequence = 0

function nextRetrievalRequestSequence(): number {
  retrievalRequestSequence += 1
  return retrievalRequestSequence
}

function readableId(value: string): string {
  return `${value.slice(0, 8)}…${value.slice(-4)}`
}

function SearchWorkspace({ tenantId }: { tenantId: string }) {
  const { authenticatedRequest } = useAuth()
  const [submission, setSubmission] = useState<{ tenantId: string; input: KnowledgeRequest; sequence: number } | null>(null)
  const activeSubmission = submission?.tenantId === tenantId ? submission : null
  const form = useForm<KnowledgeRequest>({ resolver: zodResolver(knowledgeRequestSchema), defaultValues: { query: '', top_k: 5 } })
  const search = useQuery<RetrievalResponse>({
    queryKey: tenantQueryKey(tenantId, 'retrieval', 'search', activeSubmission ? { ...activeSubmission.input, sequence: activeSubmission.sequence } : 'idle'),
    queryFn: ({ signal }) => searchDocuments(authenticatedRequest, tenantId, activeSubmission!.input, signal),
    enabled: Boolean(activeSubmission),
    retry: false,
  })

  function submit(input: KnowledgeRequest) {
    setSubmission({ tenantId, input, sequence: nextRetrievalRequestSequence() })
  }

  return <Card role="region" aria-label="Search documents">
    <CardHeader><CardTitle>Search documents</CardTitle><CardDescription>Search authorized excerpts from completed documents in the active tenant.</CardDescription></CardHeader>
    <CardContent className="space-y-5">
      <form className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem_auto] sm:items-end" noValidate onSubmit={form.handleSubmit(submit)}>
        <div className="sm:col-span-3">
          <label htmlFor="knowledge-search-query" className="mb-1.5 block text-sm font-medium">Search query</label>
          <textarea id="knowledge-search-query" rows={3} maxLength={2000} className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" placeholder="What does the leave policy say?" {...form.register('query')} />
          <p className="mt-1 text-xs text-muted-foreground">Up to 2,000 characters. Only excerpts authorized for your active tenant membership are returned.</p>
          {form.formState.errors.query && <p role="alert" className="mt-1 text-xs text-destructive">{form.formState.errors.query.message}</p>}
        </div>
        <div>
          <label htmlFor="knowledge-search-top-k" className="mb-1.5 block text-sm font-medium">Results</label>
          <input id="knowledge-search-top-k" type="number" min={1} max={20} step={1} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" {...form.register('top_k', { valueAsNumber: true })} />
          {form.formState.errors.top_k && <p role="alert" className="mt-1 text-xs text-destructive">{form.formState.errors.top_k.message}</p>}
        </div>
        <Button type="submit" disabled={search.isFetching}>{search.isFetching ? 'Searching…' : 'Search'}</Button>
      </form>

      {search.isFetching && <GlobalLoading label="Searching authorized documents" />}
      {search.isError && <div className="space-y-3"><ErrorState title="Search unavailable" message={safeKnowledgeError(search.error, 'search')} /><Button variant="outline" onClick={() => void search.refetch()}>Retry search</Button></div>}
      {search.data && <div aria-live="polite" className="space-y-4">
        <p className="text-sm text-muted-foreground">{search.data.result_count} result{search.data.result_count === 1 ? '' : 's'} returned.</p>
        {search.data.results.length === 0 && <p className="rounded-lg border border-dashed border-border p-5 text-sm text-muted-foreground">No authorized excerpts matched this query.</p>}
        <ol className="space-y-3">{search.data.results.map((result, index) => <li key={result.chunk_id}>
          <article className="rounded-xl border border-border bg-background p-4">
            <div className="flex flex-wrap items-start justify-between gap-2"><h3 className="font-semibold">Result {index + 1}</h3><span className="text-xs text-muted-foreground">Similarity {result.similarity_score.toFixed(3)}</span></div>
            <dl className="mt-3 grid gap-x-5 gap-y-2 text-xs sm:grid-cols-3">
              <div><dt className="text-muted-foreground">Document ID</dt><dd><code title={result.document_id} className="break-all">{readableId(result.document_id)}</code></dd></div>
              <div><dt className="text-muted-foreground">Chunk index</dt><dd>{result.chunk_index}</dd></div>
              <div><dt className="text-muted-foreground">Pages</dt><dd>{result.page_numbers.length ? result.page_numbers.join(', ') : 'Not provided'}</dd></div>
            </dl>
            <p className="mt-4 whitespace-pre-wrap break-words rounded-lg bg-muted/50 p-3 text-sm leading-6">{result.text}</p>
          </article>
        </li>)}</ol>
      </div>}
      {!search.data && !search.isFetching && !search.isError && <p className="text-sm text-muted-foreground">Submit a query to search authorized document excerpts.</p>}
    </CardContent>
  </Card>
}

function KnowledgeWorkspace({ tenantId, tenantName }: { tenantId: string; tenantName: string }) {
  return <div className="space-y-7">
    <header><p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">{tenantName}</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Knowledge Search</h1><p className="mt-2 text-sm text-muted-foreground">Search authorized document excerpts or ask a question grounded in this tenant’s documents.</p></header>
    <SearchWorkspace tenantId={tenantId} />
    <GenerationPanel tenantId={tenantId} />
  </div>
}

export function KnowledgePage() {
  const { activeTenant, isSwitchingTenant } = useActiveTenant()
  if (isSwitchingTenant) return <GlobalLoading label="Switching tenant workspace" />
  if (!activeTenant) return <div className="space-y-5"><header><h1 className="text-3xl font-semibold tracking-tight">Knowledge Search</h1><p className="mt-2 text-sm text-muted-foreground">Select an active tenant workspace to search its authorized documents.</p></header><Link to="/" className="font-medium text-primary hover:underline">Go to tenant workspace</Link></div>
  return <KnowledgeWorkspace key={activeTenant.tenant.id} tenantId={activeTenant.tenant.id} tenantName={activeTenant.tenant.name} />
}
