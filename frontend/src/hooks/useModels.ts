import { useQuery } from '@tanstack/react-query'
import { modelsApi } from '@/services/api'

export function useActiveModels() {
  return useQuery({
    queryKey: ['models', 'active'],
    queryFn: modelsApi.getActive,
    refetchInterval: 15_000,
  })
}

export function useCopilotModels() {
  return useQuery({
    queryKey: ['models', 'copilot'],
    queryFn: modelsApi.getCopilot,
    staleTime: 60_000,
  })
}

export function useGitHubModels() {
  return useQuery({
    queryKey: ['models', 'github'],
    queryFn: modelsApi.getGitHub,
    staleTime: 60_000,
  })
}
