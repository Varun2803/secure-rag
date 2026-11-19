import { ArrowUpRight, LockKeyhole, Workflow } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { PageContainer } from '@/components/shared/page-container'

const foundations = [
  {
    icon: LockKeyhole,
    title: 'Tenant-aware by design',
    description: 'The interface is structured to keep tenant context explicit and data views isolated.',
  },
  {
    icon: Workflow,
    title: 'Connected through one API layer',
    description: 'A typed fetch foundation keeps transport and error handling consistent across future modules.',
  },
]

export function FoundationPage() {
  return (
    <PageContainer>
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant="secondary">Frontend foundation</Badge>
        <span className="text-xs font-medium tracking-wide text-muted-foreground">PHASE 10.2</span>
      </div>
      <div className="mt-6 max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">A secure workspace, ready to grow.</h1>
        <p className="mt-4 max-w-2xl text-base leading-7 text-muted-foreground">
          SecureRAG brings company knowledge into a permission-aware workspace. This foundation establishes the visual system and application structure for future product areas.
        </p>
      </div>

      <div className="mt-9 grid gap-4 md:grid-cols-2">
        {foundations.map(({ icon: Icon, title, description }) => (
          <Card key={title}>
            <CardHeader>
              <div className="mb-2 flex size-10 items-center justify-center rounded-xl bg-accent text-primary">
                <Icon aria-hidden="true" className="size-5" />
              </div>
              <CardTitle>{title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-6 text-muted-foreground">{description}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <section aria-labelledby="next-heading" className="mt-8 rounded-2xl border border-border bg-card p-6 shadow-panel sm:p-8">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 id="next-heading" className="text-base font-semibold">A foundation before features</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Authentication, tenant workflows, documents, permissions, and retrieval will be introduced in their approved subphases.
            </p>
          </div>
          <div className="flex items-center gap-2 text-sm font-medium text-primary">
            Architecture approved <ArrowUpRight aria-hidden="true" className="size-4" />
          </div>
        </div>
      </section>
    </PageContainer>
  )
}
