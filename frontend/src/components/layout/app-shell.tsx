import { Layers3, LogOut, ShieldCheck } from 'lucide-react'
import { Link, Outlet } from 'react-router-dom'
import { ThemeToggle } from '@/app/providers/theme-provider'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/features/auth/auth-context'
import { useActiveTenant } from '@/features/tenants/active-tenant-context'
import { GlobalLoading } from '@/components/shared/global-loading'

export function AppShell() {
  const { user, logout } = useAuth()
  const { tenants, activeTenant, isTenantsLoading, isSwitchingTenant, selectTenant } = useActiveTenant()

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
              <Link className="font-medium text-primary hover:underline" to="/">Tenant workspace</Link>
            </p>
            {activeTenant && ['OWNER', 'ADMIN'].includes(activeTenant.role) && <Link className="mt-3 block text-sm font-medium text-primary hover:underline" to="/memberships">Manage memberships</Link>}
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
              <label className="sr-only" htmlFor="active-tenant-selector">Active tenant</label>
              <select id="active-tenant-selector" aria-label="Active tenant" value={activeTenant?.tenant.id ?? ''} disabled={isTenantsLoading || isSwitchingTenant || tenants.length === 0} onChange={(event) => { if (event.target.value) void selectTenant(event.target.value) }} className="h-9 max-w-48 rounded-md border border-input bg-background px-2 text-xs sm:max-w-64 sm:text-sm">
                <option value="">{isTenantsLoading ? 'Loading tenants…' : isSwitchingTenant ? 'Switching…' : tenants.length ? 'Select tenant' : 'No tenants'}</option>
                {tenants.map((item) => <option key={item.tenant.id} value={item.tenant.id}>{item.tenant.name} · {item.role}</option>)}
              </select>
              <span className="hidden max-w-40 truncate text-sm font-medium sm:block" title={user?.full_name}>{user?.full_name}</span>
              <Button aria-label="Sign out" onClick={logout} size="sm" variant="outline">
                <LogOut aria-hidden="true" className="size-4" />
                <span className="hidden sm:inline">Sign out</span>
              </Button>
              <ThemeToggle />
            </div>
          </header>
          <nav aria-label="Mobile workspace navigation" className="flex items-center gap-4 border-b border-border bg-card/40 px-4 py-3 text-sm lg:hidden">
            <Link className="font-medium text-primary hover:underline" to="/">Tenant workspace</Link>
            {activeTenant && ['OWNER', 'ADMIN'].includes(activeTenant.role) && <Link className="font-medium text-primary hover:underline" to="/memberships">Manage memberships</Link>}
          </nav>
          <main aria-busy={isSwitchingTenant} className="flex-1 px-4 py-8 sm:px-8 sm:py-10">
            <div className="mx-auto w-full max-w-6xl">
              {isSwitchingTenant ? <GlobalLoading label="Clearing previous tenant data" /> : <Outlet />}
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
