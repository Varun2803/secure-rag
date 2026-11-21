import { useQuery } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { ErrorState } from '@/components/shared/error-state'
import { GlobalLoading } from '@/components/shared/global-loading'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuth } from '@/features/auth/auth-context'
import { tenantQueryKey } from '@/features/tenants/query-keys'
import { knowledgeRequestSchema } from '@/features/retrieval/api'
import type { KnowledgeRequest } from '@/features/retrieval/api'
import { safeKnowledgeError } from '@/features/retrieval/errors'
import { generateAnswer } from './api'
import type { GenerationResponse } from './api'

let generationRequestSequence = 0

function nextGenerationRequestSequence(): number {
  generationRequestSequence += 1
  return generationRequestSequence
}

function readableId(value: string): string {
  return `${value.slice(0, 8)}…${value.slice(-4)}`
}

export function GenerationPanel({ tenantId }: { tenantId: string }) {
  const { authenticatedRequest } = useAuth()
  const [submission, setSubmission] = useState<{ tenantId: string; input: KnowledgeRequest; sequence: number } | null>(null)
  const activeSubmission = submission?.tenantId === tenantId ? submission : null
  const form = useForm<KnowledgeRequest>({ resolver: zodResolver(knowledgeRequestSchema), defaultValues: { query: '', top_k: 5 } })
  const answer = useQuery<GenerationResponse>({
    queryKey: tenantQueryKey(tenantId, 'generation', 'answer', activeSubmission ? { ...activeSubmission.input, sequence: activeSubmission.sequence } : 'idle'),
    queryFn: ({ signal }) => generateAnswer(authenticatedRequest, tenantId, activeSubmission!.input, signal),
    enabled: Boolean(activeSubmission),
    retry: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  function submit(input: KnowledgeRequest) {
    setSubmission({ tenantId, input, sequence: nextGenerationRequestSequence() })
  }

  return <Card role="region" aria-label="Ask documents">
    <CardHeader><CardTitle>Ask documents</CardTitle><CardDescription>Answers use only excerpts the backend has authorized for your active tenant membership.</CardDescription></CardHeader>
    <CardContent className="space-y-5">
      <form className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem_auto] sm:items-end" noValidate onSubmit={form.handleSubmit(submit)}>
        <div className="sm:col-span-3">
          <label htmlFor="knowledge-question" className="mb-1.5 block text-sm font-medium">Your question</label>
          <textarea id="knowledge-question" rows={3} maxLength={2000} className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" placeholder="What does the leave policy say?" {...form.register('query')} />
          <p className="mt-1 text-xs text-muted-foreground">Up to 2,000 characters. Answers may be insufficient when authorized documents do not contain the information.</p>
          {form.formState.errors.query && <p role="alert" className="mt-1 text-xs text-destructive">{form.formState.errors.query.message}</p>}
        </div>
        <div>
          <label htmlFor="knowledge-answer-top-k" className="mb-1.5 block text-sm font-medium">Context results</label>
          <input id="knowledge-answer-top-k" type="number" min={1} max={20} step={1} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" {...form.register('top_k', { valueAsNumber: true })} />
          {form.formState.errors.top_k && <p role="alert" className="mt-1 text-xs text-destructive">{form.formState.errors.top_k.message}</p>}
        </div>
        <Button type="submit" disabled={answer.isFetching}>{answer.isFetching ? 'Generating…' : 'Generate answer'}</Button>
      </form>

      {answer.isFetching && <GlobalLoading label="Retrieving authorized context and generating an answer" />}
      {answer.isError && <div className="space-y-3"><ErrorState title="Answer unavailable" message={safeKnowledgeError(answer.error, 'answer')} /><Button variant="outline" onClick={() => void answer.refetch()}>Retry answer</Button></div>}
      {answer.data && <section aria-live="polite" aria-label="Generated answer" className="space-y-4">
        <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">Answer</h3><span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${answer.data.grounded ? 'border-emerald-300 text-emerald-800 dark:text-emerald-200' : 'border-amber-300 text-amber-900 dark:text-amber-100'}`}>{answer.data.grounded ? 'Grounded' : 'Insufficient information'}</span><span className="text-xs text-muted-foreground">{answer.data.result_count} source{answer.data.result_count === 1 ? '' : 's'} returned</span></div>
        {!answer.data.grounded && <p className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">The available authorized documents did not provide sufficient information. This response is not presented as supported by citations.</p>}
        <p className="whitespace-pre-wrap break-words rounded-xl border border-border bg-background p-4 text-sm leading-7">{answer.data.answer}</p>
        {answer.data.grounded && answer.data.citations.length > 0 && <section aria-label="Answer citations" className="space-y-3">
          <h4 className="text-sm font-semibold">Sources returned by the backend</h4>
          <ol className="grid gap-3 md:grid-cols-2">{answer.data.citations.map((citation, index) => <li key={`${citation.chunk_id}-${index}`}>
            <article className="h-full rounded-lg border border-border p-3">
              <h5 className="text-sm font-semibold">[S{index + 1}]</h5>
              <dl className="mt-2 space-y-2 text-xs">
                <div><dt className="text-muted-foreground">Document ID</dt><dd><code title={citation.document_id} className="break-all">{readableId(citation.document_id)}</code></dd></div>
                <div><dt className="text-muted-foreground">Pages</dt><dd>{citation.page_numbers.length ? citation.page_numbers.join(', ') : 'Not provided'}</dd></div>
                <div><dt className="text-muted-foreground">Relevance score</dt><dd>{citation.relevance_score.toFixed(3)}</dd></div>
              </dl>
            </article>
          </li>)}</ol>
          <p className="text-xs text-muted-foreground">Citation labels follow the backend’s returned citation order. Document identifiers and pages come only from validated backend metadata.</p>
        </section>}
      </section>}
      {!answer.data && !answer.isFetching && !answer.isError && <p className="text-sm text-muted-foreground">Submit a question to receive an answer based only on authorized document context.</p>}
    </CardContent>
  </Card>
}
