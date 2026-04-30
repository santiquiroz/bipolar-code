import { useQuery } from '@tanstack/react-query'
import { capabilitiesApi } from '@/services/api'

export interface ModelCapabilities {
  context_window: number
  supports_vision: boolean
  supports_tools: boolean
}

export function useCapabilities() {
  return useQuery<Record<string, ModelCapabilities>>({
    queryKey: ['capabilities'],
    queryFn: capabilitiesApi.getAll,
    staleTime: 1000 * 60 * 60, // 1 hour
  })
}

export function useModelCapabilities(modelId: string | undefined) {
  const { data: all } = useCapabilities()
  if (!modelId || !all) return undefined
  return (
    all[modelId] ||
    Object.entries(all).find(([key]) => key.endsWith('/*') && modelId.startsWith(key.slice(0, -2)))?.[1] ||
    all['__default__']
  )
}
