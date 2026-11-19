import { Layers3, ShieldCheck } from 'lucide-react'
import { Outlet } from 'react-router-dom'
import { ThemeToggle } from '@/app/providers/theme-provider'

export function AppShell() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex min-h-screen max-w-[1600px]">
        <aside className="hidden w-64 shrink-0 border-r border-border bg-card/70 p-5 lg:flex lg:flex-col">
          <a className="flex items-center gap-3 rounded-lg px-2 py-2 outline-none focus-visible:ring-2 focus-visible:ring-ring" href="/">
            <span className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
              <ShieldCheck aria-hidden="true" className="size-5" />
            </span>
            <span>
              <span className="block text-sm font-semibold tracking-tight">SecureRAG</span>
              <span className="block text-xs text-muted-foreground">Secure knowledge workspace</span>
            </span>
          </a>

          <div className="mt-12 rounded-xl border border-dashed border-border bg-background/70 p-4">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <Layers3 aria-hidden="true" className="size-4" />
              Workspace
            </div>
            <p className="mb-0 mt-3 text-sm leading-6 text-muted-foreground">
              Workspace modules will appear here when they are implemented.
            </p>
          </div>

          <div className="mt-auto border-t border-border pt-4 text-xs leading-5 text-muted-foreground">
            Foundation environment
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-16 items-center justify-between border-b border-border bg-card/60 px-5 sm:px-8">
            <div className="flex items-center gap-3 lg:hidden">
              <span className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                <ShieldCheck aria-hidden="true" className="size-5" />
              </span>
              <span className="text-sm font-semibold">SecureRAG</span>
            </div>
            <p className="hidden text-sm text-muted-foreground lg:block">Enterprise knowledge security</p>
            <div className="ml-auto flex items-center gap-3">
              <span className="hidden rounded-full border border-border bg-background px-3 py-1 text-xs font-medium text-muted-foreground sm:inline-flex">
                Foundation
              </span>
              <ThemeToggle />
            </div>
          </header>
          <main className="flex-1 px-4 py-8 sm:px-8 sm:py-10">
            <div className="mx-auto w-full max-w-6xl">
              <Outlet />
            </div>
          </main>
          <footer className="border-t border-border px-5 py-4 text-center text-xs text-muted-foreground sm:px-8">
            SecureRAG · Permission-aware knowledge workspace
          </footer>
        </div>
      </div>
    </div>
  )
}
