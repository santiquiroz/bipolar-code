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

export function useLlamaLogs(enabled: boolean) {
  return useQuery({
    queryKey: ['llamacpp', 'logs'],
    queryFn: () => llamacppApi.getLogs(120),
    enabled,
    refetchInterval: 3_000,
  })
}

export function useHFDownloads(enabled = true) {
  return useQuery({
    queryKey: ['llamacpp', 'downloads'],
    queryFn: llamacppApi.getDownloads,
    enabled,
    refetchInterval: 2_000,
  })
}

export function useLocalModels() {
  return useQuery({
    queryKey: ['llamacpp', 'local-models'],
    queryFn: llamacppApi.getLocalModels,
    staleTime: 10_000,
  })
}

export function useStartDownload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ repoId, filename }: { repoId: string; filename: string }) =>
      llamacppApi.download(repoId, filename),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['llamacpp', 'downloads'] }),
  })
}

export function useUseModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (path: string) => llamacppApi.useModel(path),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['providers'] }),
  })
}
