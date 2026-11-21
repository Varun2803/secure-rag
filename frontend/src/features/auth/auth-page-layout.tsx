import { ShieldCheck, Sparkles } from 'lucide-react'
import type { PropsWithChildren } from 'react'
import { ThemeToggle } from '@/app/providers/theme-provider'

export function AuthPageLayout({ children, eyebrow, title, description }: PropsWithChildren<{
  eyebrow: string
  title: string
  description: string
}>) {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="flex h-16 items-center justify-between border-b border-border px-5 sm:px-8">
        <a href="/" className="flex items-center gap-3 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <ShieldCheck aria-hidden="true" className="size-5" />
          </span>
          <span className="text-sm font-semibold tracking-tight">SecureRAG</span>
        </a>
        <ThemeToggle />
      </header>

      <div className="mx-auto grid min-h-[calc(100vh-4rem)] max-w-7xl items-center gap-10 px-5 py-10 lg:grid-cols-[1fr_460px] lg:px-10">
        <section className="hidden max-w-2xl lg:block">
          <div className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground">
            <Sparkles aria-hidden="true" className="size-3.5 text-primary" />
            Permission-aware company knowledge
          </div>
          <h1 className="mt-7 text-4xl font-semibold leading-tight tracking-tight xl:text-5xl">
            Your company knowledge, protected by design.
          </h1>
          <p className="mt-5 max-w-xl text-base leading-7 text-muted-foreground">
            SecureRAG keeps company workspaces separate and respects document access rules when people search and ask questions.
          </p>
          <div className="mt-10 grid max-w-xl grid-cols-2 gap-3">
            <div className="rounded-xl border border-border bg-card/80 p-4">
              <p className="text-sm font-semibold">Private by default</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">Access follows explicit workspace membership and document permissions.</p>
            </div>
            <div className="rounded-xl border border-border bg-card/80 p-4">
              <p className="text-sm font-semibold">Grounded answers</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">Responses are based on information the signed-in user can access.</p>
            </div>
          </div>
        </section>

        <section className="w-full max-w-md justify-self-center lg:justify-self-end">
          <div className="mb-7 lg:hidden">
            <p className="text-sm font-medium text-primary">Secure knowledge workspace</p>
          </div>
          <div className="rounded-2xl border border-border bg-card p-6 shadow-panel sm:p-8">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">{eyebrow}</p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
            <div className="mt-7">{children}</div>
          </div>
          <p className="mt-5 text-center text-xs text-muted-foreground">SecureRAG · Permission-aware knowledge workspace</p>
        </section>
      </div>
    </main>
  )
}
