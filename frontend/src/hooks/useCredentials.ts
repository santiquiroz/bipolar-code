import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { providersApi, settingsApi } from '@/services/api'
import type { Provider } from '@/types/provider'

export function nextPoolVarName(authVar: string, extras: string[]): string {
  let n = 2
  let name = `${authVar}_${n}`
  while (name === authVar || extras.includes(name)) {
    n += 1
    name = `${authVar}_${n}`
  }
  return name
}

export function useProviderCredentials(providerId: string) {
  return useQuery({
    queryKey: ['credentials', providerId],
    queryFn: () => providersApi.credentials(providerId),
    enabled: !!providerId,
  })
}

export function useAddPoolKey(provider: Provider) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ value }: { value: string }) => {
      const extras = provider.extra_auth_env_vars ?? []
      const name = nextPoolVarName(provider.auth_env_var, extras)
      await settingsApi.setEnvKey(name, value)
      return providersApi.update(provider.id, { extra_auth_env_vars: [...extras, name] })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['providers'] })
      qc.invalidateQueries({ queryKey: ['credentials', provider.id] })
    },
  })
}

export function useRemovePoolKey(provider: Provider) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) =>
      providersApi.update(provider.id, {
        extra_auth_env_vars: (provider.extra_auth_env_vars ?? []).filter((n) => n !== name),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['providers'] })
      qc.invalidateQueries({ queryKey: ['credentials', provider.id] })
    },
  })
}
