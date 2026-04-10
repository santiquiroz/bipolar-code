import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { proxyApi } from '@/services/api'

export function useProxyRoute() {
  const qc = useQueryClient()
  const route = useQuery({ queryKey: ['proxy','route'], queryFn: proxyApi.getRoute, refetchInterval: 5000 })
  const setRoute = useMutation({
    mutationFn: (mode: 'direct'|'proxy') => proxyApi.setRoute(mode),
    onSuccess: (_data, mode) => {
      // optimistic update so button switches instantly, refetch confirms
      qc.setQueryData(['proxy', 'route'], (old: any) => ({ ...(old ?? {}), mode }))
      qc.invalidateQueries({ queryKey: ['proxy', 'route'] })
      qc.invalidateQueries({ queryKey: ['proxy'] })
    },
  })
  return { route, setRoute }
}
