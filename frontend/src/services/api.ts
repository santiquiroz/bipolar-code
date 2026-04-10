import axios from 'axios'
import type { ProxyStatus, ModelEntry, CopilotModel, UsageStats, BackendType } from '@/types'

const api = axios.create({ baseURL: '/api' })

api.interceptors.response.use(
  (res) => res,
  (err) => {
    console.error('[api]', err.config?.url, err.response?.status, err.response?.data)
    return Promise.reject(err)
  }
)

export const proxyApi = {
  getStatus: () => api.get<ProxyStatus>('/proxy/status').then(r => r.data),
  switchBackend: (backend: BackendType) =>
    api.post('/proxy/switch', { backend }).then(r => r.data),
}

export const modelsApi = {
  getActive: () => api.get<ModelEntry[]>('/models/active').then(r => r.data),
  getCopilot: () => api.get<CopilotModel[]>('/models/copilot').then(r => r.data),
  getGitHub: () => api.get<CopilotModel[]>('/models/github').then(r => r.data),
}

export const usageApi = {
  getAnthropicUsage: () => api.get<UsageStats[]>('/usage/anthropic').then(r => r.data),
  getLogStats: () => api.get<UsageStats[]>('/usage/logs').then(r => r.data),
}
