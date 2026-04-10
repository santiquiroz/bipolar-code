import { useQuery } from '@tanstack/react-query'
import { modelsApi } from '@/services/api'

export function useActiveModels() {
  return useQuery({
    queryKey: ['models', 'active'],
    queryFn: modelsApi.getActive,
    refetchInterval: 15_000,
  })
}
