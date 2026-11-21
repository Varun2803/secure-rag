import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PropsWithChildren } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/features/auth/auth-context'
import { ActiveTenantContext } from './active-tenant-context'
import type { TenantListItem } from './api'
import { listTenants } from './api'

import { tenantListQueryKey } from './query-keys'

export function ActiveTenantProvider({ children }: PropsWithChildren) {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [activeTenantId, setActiveTenantId] = useState<string | null>(null)
  const [isSwitchingTenant, setIsSwitchingTenant] = useState(false)
  const [unavailableTenantNotice, setUnavailableTenantNotice] = useState(false)
  const switchSequence = useRef(0)
  const activeTenantIdRef = useRef<string | null>(null)
  const tenantsQuery = useQuery({
    queryKey: tenantListQueryKey,
    queryFn: ({ signal }) => listTenants(auth.authenticatedRequest, signal),
    enabled: auth.status === 'authenticated',
    staleTime: 30_000,
  })
  const refetchTenants = tenantsQuery.refetch

  const setActiveId = useCallback((tenantId: string | null) => {
    activeTenantIdRef.current = tenantId
    setActiveTenantId(tenantId)
  }, [])

  useEffect(() => {
    if (auth.status === 'authenticated') return
    void queryClient.cancelQueries({ predicate: (query) => query.queryKey[0] === 'tenant' || query.queryKey[0] === 'tenants' })
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] === 'tenant' || query.queryKey[0] === 'tenants' })
  }, [auth.status, queryClient])

  const selectTenant = useCallback(async (tenantId: string) => {
    const available = queryClient.getQueryData<TenantListItem[]>(tenantListQueryKey)?.some((item) => item.tenant.id === tenantId)
    if (!available) return
    const previousId = activeTenantIdRef.current
    if (previousId === tenantId) return

    const sequence = ++switchSequence.current
    setIsSwitchingTenant(true)
    setUnavailableTenantNotice(false)
    // Hide the previous workspace immediately while cancellation is in flight,
    // but retain its ID until its requests and cache have been cleared.
    setActiveTenantId(null)
    if (previousId) {
      await queryClient.cancelQueries({ predicate: (query) => query.queryKey[0] === 'tenant' && query.queryKey[1] === previousId })
      if (sequence !== switchSequence.current) return
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] === 'tenant' && query.queryKey[1] === previousId })
    }
    if (sequence !== switchSequence.current) return
    setActiveId(tenantId)
    setIsSwitchingTenant(false)
    navigate('/', { replace: true })
  }, [navigate, queryClient, setActiveId])

  const clearUnavailableTenant = useCallback((tenantId: string) => {
    if (activeTenantIdRef.current !== tenantId) return
    switchSequence.current += 1
    setActiveId(null)
    setIsSwitchingTenant(false)
    setUnavailableTenantNotice(true)
    void queryClient.cancelQueries({ predicate: (query) => query.queryKey[0] === 'tenant' && query.queryKey[1] === tenantId })
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] === 'tenant' && query.queryKey[1] === tenantId })
    void refetchTenants()
  }, [queryClient, refetchTenants, setActiveId])

  const refreshTenants = useCallback(async () => {
    const result = await refetchTenants()
    return result.data ?? []
  }, [refetchTenants])

  const tenants: TenantListItem[] = useMemo(() => tenantsQuery.data ?? [], [tenantsQuery.data])
  const activeTenant = tenants.find((item) => item.tenant.id === activeTenantId) ?? null
  const value = useMemo(() => ({
    tenants,
    activeTenant,
    isTenantsLoading: tenantsQuery.isLoading,
    tenantsError: tenantsQuery.isError,
    unavailableTenantNotice,
    isSwitchingTenant,
    refreshTenants,
    selectTenant,
    clearUnavailableTenant,
  }), [activeTenant, clearUnavailableTenant, isSwitchingTenant, refreshTenants, selectTenant, tenants, tenantsQuery.isError, tenantsQuery.isLoading, unavailableTenantNotice])

  return <ActiveTenantContext.Provider value={value}>{children}</ActiveTenantContext.Provider>
}
