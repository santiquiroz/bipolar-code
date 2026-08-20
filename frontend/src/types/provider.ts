export interface Provider {
  id: string
  name: string
  description: string
  api_base: string
  litellm_prefix: string
  auth_env_var: string
  extra_headers: Record<string, string>
  models_endpoint: string | null
  models_auth_env_var: string
  active_model: string
  model_info: Record<string, unknown>
  drop_params: boolean
  use_chat_completions_for_anthropic: boolean
  max_tools: number
  rate_limit_rpm: number
  anthropic_native?: boolean
  local_launch?: LocalLaunchConfig
}

export interface LocalLaunchConfig {
  exe_path?: string
  model_path?: string
  ctx_size?: number
  split_mode?: string
  tensor_split?: 'auto' | number[]
  ngl?: number
  extra_args?: string[]
  autostart?: boolean
  router_mode?: boolean
  models_dir?: string
}

export interface LlamaDevice {
  index: number
  backend: string
  name: string
  vram_total_mib: number
  vram_free_mib: number
}

export interface LlamaDevicesResponse {
  exe_found: boolean
  exe_path: string
  devices: LlamaDevice[]
}

export interface LlamaStatus {
  running: boolean
  pid: number | null
  port: number | null
  model_path: string | null
  healthy: boolean
  busy_slots: number
}

export interface HFRepo {
  id: string
  downloads: number
  likes: number
  updated: string
}

export interface HFFile {
  filename: string
  size: number
  parts: string[]
}

export interface HFDownload {
  id: string
  repo_id: string
  filename: string
  parts: string[]
  status: 'queued' | 'downloading' | 'done' | 'error' | 'cancelled'
  total_bytes: number
  downloaded_bytes: number
  speed_bps: number
  error: string
}

export interface LocalModel {
  filename: string
  path: string
  size: number
}

export interface RoutingRule {
  pattern: string
  min_tokens: number
  provider_id: string
  model: string
}

export interface RoutingConfig {
  enabled: boolean
  rules: RoutingRule[]
  fallback_provider_ids?: string[]
}

export interface ProviderRegistry {
  active_provider_id: string
  providers: Provider[]
}

export interface ProviderModel {
  id: string
  name: string
  vendor?: string
}
