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
