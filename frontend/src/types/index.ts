export type BackendType = 'copilot' | 'claude' | 'gemma' | 'custom'

export interface ProxyStatus {
  running: boolean
  port: number
  active_backend: BackendType
  config_file: string
  healthy_models: number
  unhealthy_models: number
}

export interface ModelEntry {
  model_name: string
  backend: BackendType
  api_base: string
  is_healthy: boolean
  latency_ms?: number
}

export interface CopilotModel {
  id: string
  name: string
  vendor?: string
  version?: string
  capabilities?: Record<string, unknown>
}

export interface EnvVars {
  [key: string]: string
}

export interface UsageStats {
  backend: BackendType
  model: string
  input_tokens?: number
  output_tokens?: number
  estimated_cost_usd?: number
  requests_count: number
  note?: string
}
