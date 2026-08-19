import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { llamacppApi } from '@/services/api'

export function useLlamaDevices(enabled = true) {
  return useQuery({
    queryKey: ['llamacpp', 'devices'],
    queryFn: llamacppApi.getDevices,
    enabled,
    staleTime: 30_000,
  })
}

export function useLlamaStatus(enabled = true) {
  return useQuery({
    queryKey: ['llamacpp', 'status'],
    queryFn: llamacppApi.getStatus,
    enabled,
    refetchInterval: 5_000,
  })
}

export function useStartLlama() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => llamacppApi.start(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['llamacpp', 'status'] }),
  })
}

export function useStopLlama() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (force: boolean) => llamacppApi.stop(force),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['llamacpp', 'status'] }),
  })
}
