import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { providersApi } from '@/services/api'
import type { Provider } from '@/types/provider'

export function useProviders() {
  return useQuery({
    queryKey: ['providers'],
    queryFn: providersApi.list,
    refetchInterval: 15_000,
  })
}

export function useProviderModels(providerId: string, enabled = true) {
  return useQuery({
    queryKey: ['providers', providerId, 'models'],
    queryFn: () => providersApi.listModels(providerId),
    enabled: enabled && !!providerId,
    staleTime: 60_000,
  })
}

export function useSwitchProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (provider_id: string) => providersApi.switch(provider_id),
    onSuccess: () => {
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['providers'] })
        qc.invalidateQueries({ queryKey: ['proxy'] })
      }, 3000)
    },
  })
}

export function useSetProviderModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ provider_id, model_id }: { provider_id: string; model_id: string }) =>
      providersApi.setModel(provider_id, model_id),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['providers'] })
      qc.invalidateQueries({ queryKey: ['providers', vars.provider_id, 'models'] })
    },
  })
}

export function useAddProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (provider: Partial<Provider>) => providersApi.add(provider),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['providers'] }),
  })
}

export function useDeleteProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => providersApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['providers'] }),
  })
}
