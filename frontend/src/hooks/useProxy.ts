import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { proxyApi } from '@/services/api'

export function useProxyStatus() {
  return useQuery({
    queryKey: ['proxy', 'status'],
    queryFn: proxyApi.getStatus,
    refetchInterval: 10_000,
  })
}

export function useStartProxy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: proxyApi.start,
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ['proxy'] }), 4000)
    },
  })
}
