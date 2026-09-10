import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { delegateApi } from '@/services/api'
import type { JobRequest } from '@/types/smart'

export function useJobs() {
  const query = useQuery({ queryKey: ['delegate', 'jobs'], queryFn: () => delegateApi.listJobs({ limit: 50 }), refetchInterval: (q) => {
    const jobs = q.state.data?.jobs || []
    return jobs.some(j => j.status === 'queued' || j.status === 'running') ? 5_000 : 30_000
  } })
  return query
}
export function useSubmitJob() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: JobRequest) => delegateApi.submitJob(body), onSuccess: () => qc.invalidateQueries({ queryKey: ['delegate', 'jobs'] }) })
}
export function useCancelJob() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: delegateApi.cancelJob, onSuccess: () => qc.invalidateQueries({ queryKey: ['delegate', 'jobs'] }) })
}
