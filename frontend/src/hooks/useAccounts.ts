import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { accountsApi } from '@/services/api'

export function useAccounts() {
  return useQuery({ queryKey: ['accounts'], queryFn: accountsApi.list, refetchInterval: 15_000 })
}
export function useCreateAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ base, label }: { base: string; label: string }) => accountsApi.create(base, label),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['accounts'] }); qc.invalidateQueries({ queryKey: ['smart'] }) },
  })
}
export function useDeleteAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (agentId: string) => accountsApi.remove(agentId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['accounts'] }); qc.invalidateQueries({ queryKey: ['smart'] }) },
  })
}
