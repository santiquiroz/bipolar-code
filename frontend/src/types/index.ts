export interface ModelEntry {
  model_name: string
  provider_id: string
  api_base: string
  is_healthy: boolean
  latency_ms?: number
}

export interface UsageStats {
  provider_id: string
  model: string
  input_tokens?: number
  output_tokens?: number
  estimated_cost_usd?: number
  requests_count: number
  note?: string
}

export interface EnvVars {
  [key: string]: string
}
