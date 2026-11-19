import { LoaderCircle } from 'lucide-react'

export function GlobalLoading({ label = 'Loading' }: { label?: string }) {
  return (
    <div aria-live="polite" className="flex min-h-40 items-center justify-center gap-3 text-sm text-muted-foreground" role="status">
      <LoaderCircle aria-hidden="true" className="size-5 animate-spin text-primary" />
      <span>{label}</span>
    </div>
  )
}
