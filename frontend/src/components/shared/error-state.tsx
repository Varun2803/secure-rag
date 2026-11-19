import { CircleAlert } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'

export function ErrorState({ title = 'Something went wrong', message = 'Please try again in a moment.' }: { title?: string; message?: string }) {
  return (
    <Card role="alert" className="border-destructive/30">
      <CardContent className="flex items-start gap-3 pt-6">
        <CircleAlert aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-destructive" />
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{message}</p>
        </div>
      </CardContent>
    </Card>
  )
}
