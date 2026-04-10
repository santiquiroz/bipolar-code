import { useQuery } from '@tanstack/react-query'
import { usageApi } from '@/services/api'

export function useAnthropicUsage() {
  return useQuery({
    queryKey: ['usage', 'anthropic'],
    queryFn: usageApi.getAnthropicUsage,
    staleTime: 60_000,
  })
}

export function useLogStats() {
  return useQuery({
    queryKey: ['usage', 'logs'],
    queryFn: usageApi.getLogStats,
    staleTime: 30_000,
  })
}
