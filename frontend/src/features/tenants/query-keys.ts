export const tenantListQueryKey = ['tenants'] as const
export const tenantQueryKey = (tenantId: string, ...parts: readonly unknown[]) => ['tenant', tenantId, ...parts] as const
