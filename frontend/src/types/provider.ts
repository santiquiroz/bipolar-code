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
