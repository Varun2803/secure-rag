import { createContext, useContext } from 'react'
import type { TenantListItem } from './api'

export type ActiveTenantContextValue = {
  tenants: TenantListItem[]
  activeTenant: TenantListItem | null
  isTenantsLoading: boolean
  tenantsError: boolean
  unavailableTenantNotice: boolean
  isSwitchingTenant: boolean
  refreshTenants: () => Promise<TenantListItem[]>
  selectTenant: (tenantId: string) => Promise<void>
  clearUnavailableTenant: (tenantId: string) => void
}

export const ActiveTenantContext = createContext<ActiveTenantContextValue | null>(null)

export function useActiveTenant() {
  const context = useContext(ActiveTenantContext)
  if (!context) throw new Error('useActiveTenant must be used within ActiveTenantProvider')
  return context
}
