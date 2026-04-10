import axios from 'axios'
import type { ModelEntry, UsageStats } from '@/types'
import type { Provider, ProviderRegistry, ProviderModel } from '@/types/provider'

const api = axios.create({ baseURL: '/api' })

api.interceptors.response.use(
  (res) => res,
  (err) => {
    console.error('[api]', err.config?.url, err.response?.status, err.response?.data)
    return Promise.reject(err)
  }
)

export const proxyApi = {
  getStatus: () => api.get<{ running: boolean; port: number; active_provider_id: string; healthy_models: number; unhealthy_models: number }>('/proxy/status').then(r => r.data),
}

export const providersApi = {
  list: () => api.get<ProviderRegistry>('/providers').then(r => r.data),
  get: (id: string) => api.get<Provider>(`/providers/${id}`).then(r => r.data),
  add: (provider: Partial<Provider>) => api.post<Provider>('/providers', provider).then(r => r.data),
  update: (id: string, updates: Partial<Provider>) => api.patch<Provider>(`/providers/${id}`, updates).then(r => r.data),
  delete: (id: string) => api.delete(`/providers/${id}`).then(r => r.data),
  switch: (provider_id: string) => api.post('/providers/switch', { provider_id }).then(r => r.data),
  setModel: (provider_id: string, model_id: string) =>
    api.post<Provider>(`/providers/${provider_id}/model`, { model_id }).then(r => r.data),
  listModels: (provider_id: string) =>
    api.get<{ models: ProviderModel[]; note?: string }>(`/providers/${provider_id}/models`).then(r => r.data),
}

export const modelsApi = {
  getActive: () => api.get<ModelEntry[]>('/models/active').then(r => r.data),
}

export const settingsApi = {
  getEnv: () => api.get<Record<string, string>>('/settings/env').then(r => r.data),
  setEnvKey: (key: string, value: string) =>
    api.post('/settings/env', { key, value }).then(r => r.data),
}

export const usageApi = {
  getAnthropicUsage: () => api.get<UsageStats[]>('/usage/anthropic').then(r => r.data),
  getLogStats: () => api.get<UsageStats[]>('/usage/logs').then(r => r.data),
}
