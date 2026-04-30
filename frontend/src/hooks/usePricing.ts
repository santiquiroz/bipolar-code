import { useQuery } from '@tanstack/react-query'
import { pricingApi } from '@/services/api'

export interface ModelPrice {
  input: number
  output: number
  cache_read?: number
  cache_write?: number
}

export interface PricingData {
  version: string
  providers: Record<string, Record<string, ModelPrice | boolean | string>>
}

export function usePricing() {
  return useQuery<PricingData>({
    queryKey: ['pricing'],
    queryFn: pricingApi.getAll,
    staleTime: 1000 * 60 * 10,
  })
}

export function useModelPrice(providerId: string | undefined, modelId: string | undefined) {
  const { data: pricing } = usePricing()
  if (!providerId || !modelId || !pricing) return null
  const providerPricing = pricing.providers[providerId]
  if (!providerPricing) return null
  if (providerPricing['__free__'] || providerPricing['__flat_rate__']) return 'free' as const
  if (providerPricing['__dynamic__']) return null
  return (providerPricing[modelId] as ModelPrice) || null
}

export function formatPrice(price: ModelPrice | 'free' | null): string {
  if (price === 'free') return 'GRATIS'
  if (!price) return '—'
  return `$${price.input.toFixed(2)} / $${price.output.toFixed(2)}`
}
