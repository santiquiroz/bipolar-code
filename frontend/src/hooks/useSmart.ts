import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { smartApi } from '@/services/api'
import type { SmartRoutingConfig, CliAgent, DelegationConfig } from '@/types/smart'

export function useSmartConfig() {
  return useQuery({ queryKey: ['smart'], queryFn: () => smartApi.getConfig(), refetchInterval: 30_000 })
}
export function useSaveSmartConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { smart?: SmartRoutingConfig; cli_agents?: CliAgent[]; delegation?: DelegationConfig }) => smartApi.saveConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['smart'] }); qc.invalidateQueries({ queryKey: ['routing'] }) },
  })
}
export function useSmartHealth() {
  return useQuery({ queryKey: ['smart', 'health'], queryFn: smartApi.getHealth, refetchInterval: 10_000 })
}
export function useDecisions(params?: { limit?: number; tier?: string; target?: string; source?: string; since?: string }) {
  return useQuery({ queryKey: ['smart', 'decisions', params], queryFn: () => smartApi.getDecisions(params), refetchInterval: 15_000 })
}
export function useDecisionsSummary(period: 'day' | 'week' | 'month' = 'day') {
  return useQuery({ queryKey: ['smart', 'decisions-summary', period], queryFn: () => smartApi.getDecisionsSummary(period), refetchInterval: 30_000 })
}
export function useAgents() {
  return useQuery({ queryKey: ['smart', 'agents'], queryFn: () => smartApi.getAgents(), refetchInterval: 15_000 })
}
export function useProbeAgent() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: smartApi.probeAgent, onSuccess: () => qc.invalidateQueries({ queryKey: ['smart'] }) })
}
export function useResetHealth() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: smartApi.resetHealth, onSuccess: () => qc.invalidateQueries({ queryKey: ['smart'] }) })
}
export function useClassify() { return useMutation({ mutationFn: smartApi.classify }) }
export function useExplain() { return useMutation({ mutationFn: smartApi.explain }) }
