import { useQuery } from '@tanstack/react-query'
import { proxyApi } from '@/services/api'

export function useProxyStatus() {
  return useQuery({
    queryKey: ['proxy', 'status'],
    queryFn: proxyApi.getStatus,
    refetchInterval: 10_000,
  })
}
