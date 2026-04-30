import { useQuery } from '@tanstack/react-query'
import { usageApi, usageHistoryApi } from '@/services/api'

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

export function useUsageHistory(params?: { provider?: string; limit?: number }) {
  return useQuery({
    queryKey: ['usage', 'history', params],
    queryFn: () => usageHistoryApi.getHistory(params),
    refetchInterval: 30_000,
  })
}

export function useUsageSummary(period: 'day' | 'week' | 'month' = 'day') {
  return useQuery({
    queryKey: ['usage', 'summary', period],
    queryFn: () => usageHistoryApi.getSummary(period),
    refetchInterval: 30_000,
  })
}
