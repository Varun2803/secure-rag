import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { PageContainer } from './page-container'

export function NotFoundPage() {
  return (
    <PageContainer className="py-16 text-center">
      <p className="text-sm font-semibold text-primary">404</p>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="mt-2 text-sm text-muted-foreground">That page is not part of this workspace.</p>
      <Button asChild className="mt-6"><Link to="/">Return to workspace</Link></Button>
    </PageContainer>
  )
}
