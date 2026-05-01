import axios from 'axios'
import type { ModelEntry, UsageStats } from '@/types'
import type { Provider, ProviderRegistry, ProviderModel } from '@/types/provider'

const STORAGE_KEY = 'bipolar_api_key'

export function getStoredApiKey(): string {
  return localStorage.getItem(STORAGE_KEY) ?? ''
}

export function setStoredApiKey(key: string): void {
  localStorage.setItem(STORAGE_KEY, key)
}

export function clearStoredApiKey(): void {
  localStorage.removeItem(STORAGE_KEY)
}

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const key = getStoredApiKey()
  if (key) {
    config.headers['X-API-Key'] = key
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    console.error('[api]', err.config?.url, err.response?.status, err.response?.data)
    return Promise.reject(err)
  }
)

export async function validateApiKey(key: string): Promise<boolean> {
  try {
    const resp = await axios.get('/api/providers', {
      headers: { 'X-API-Key': key },
    })
    return resp.status === 200
  } catch {
    return false
  }
}

export const proxyApi = {
  getStatus: () => api.get<{ running: boolean; port: number; active_provider_id: string; healthy_models: number; unhealthy_models: number }>('/proxy/status').then(r => r.data),
  start: () => api.post<{ started: boolean; provider: string }>('/proxy/start').then(r => r.data),
  getRoute: () => api.get<{ mode: string; litellm_running: boolean; proxy_status: any }>('/proxy/route').then(r => r.data),
  setRoute: (mode: 'direct' | 'proxy') => api.post('/proxy/route', { mode }).then(r => r.data),
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
  refreshToken: (provider_id: string) =>
    api.post<{ refreshed: boolean; note?: string; token_length?: number }>(`/providers/${provider_id}/refresh-token`).then(r => r.data),
}

export const modelsApi = {
  getActive: () => api.get<ModelEntry[]>('/models/active').then(r => r.data),
}

export const settingsApi = {
  getEnv: () => api.get<Record<string, string>>('/settings/env').then(r => r.data),
  setEnvKey: (key: string, value: string) =>
    api.post('/settings/env', { key, value }).then(r => r.data),
  getAuthInfo: () => api.get<{
    api_key_prefix: string
    api_key_length: number
    rate_limit_rpm: number
    allowed_origins: string
  }>('/settings/auth-info').then(r => r.data),
  getApiKey: () => api.get<{ api_key: string }>('/settings/api-key').then(r => r.data),
}

export const usageApi = {
  getAnthropicUsage: () => api.get<UsageStats[]>('/usage/anthropic').then(r => r.data),
  getLogStats: () => api.get<UsageStats[]>('/usage/logs').then(r => r.data),
}

export const capabilitiesApi = {
  getAll: () => api.get('/models/capabilities').then(r => r.data),
  getModel: (modelId: string) =>
    api.get(`/models/capabilities/${encodeURIComponent(modelId)}`).then(r => r.data),
}

export const pricingApi = {
  getAll: () => api.get('/pricing/models').then(r => r.data),
  getModel: (providerId: string, modelId: string) =>
    api.get(`/pricing/model/${providerId}/${encodeURIComponent(modelId)}`).then(r => r.data),
}

export const usageHistoryApi = {
  getHistory: (params?: { provider?: string; model?: string; limit?: number }) =>
    api.get('/usage/history', { params }).then(r => r.data),
  getSummary: (period: 'day' | 'week' | 'month' = 'day') =>
    api.get('/usage/summary', { params: { period } }).then(r => r.data),
}

export const verifyKeyApi = {
  verify: (providerId: string, apiKey: string) =>
    api.post(`/providers/${providerId}/verify-key`, { api_key: apiKey }).then(r => r.data),
}
