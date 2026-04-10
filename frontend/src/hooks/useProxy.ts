import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { proxyApi } from '@/services/api'
import type { BackendType } from '@/types'

export function useProxyStatus() {
  return useQuery({
    queryKey: ['proxy', 'status'],
    queryFn: proxyApi.getStatus,
    refetchInterval: 10_000,
  })
}

export function useSwitchBackend() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (backend: BackendType) => proxyApi.switchBackend(backend),
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ['proxy'] }), 3000)
    },
  })
}
