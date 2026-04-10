import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { settingsApi } from '@/services/api'

export function useEnvVars() {
  return useQuery({
    queryKey: ['settings', 'env'],
    queryFn: settingsApi.getEnv,
    staleTime: 30_000,
  })
}

export function useSetEnvKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      settingsApi.setEnvKey(key, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'env'] }),
  })
}

export function useCopilotActiveModel() {
  return useQuery({
    queryKey: ['settings', 'copilot-model'],
    queryFn: settingsApi.getCopilotActiveModel,
    staleTime: 10_000,
  })
}

export function useSetCopilotModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (model_id: string) => settingsApi.setCopilotModel(model_id),
    onSuccess: () => {
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['settings', 'copilot-model'] })
        qc.invalidateQueries({ queryKey: ['proxy'] })
      }, 3000)
    },
  })
}
