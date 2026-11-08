import { useQuery } from '@tanstack/react-query'
import { healthApi } from '@/services/api'

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: healthApi.get, retry: false, staleTime: 300_000 })
}
