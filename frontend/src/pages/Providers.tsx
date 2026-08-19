import { useState } from 'react'
import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Spinner } from '@/components/Spinner'
import { AddProviderModal } from '@/components/AddProviderModal'
import { NvidiaWizard } from '@/components/NvidiaWizard'
import { LlamaCppPanel } from '@/components/LlamaCppPanel'
import { RoutingPanel } from '@/components/RoutingPanel'
import { useProviders, useSwitchProvider, useDeleteProvider } from '@/hooks/useProviders'
import { useQuery } from '@tanstack/react-query'
import { settingsApi } from '@/services/api'
import type { Provider } from '@/types/provider'

export function Providers() {
  const { data: registry, isLoading } = useProviders()
  const switchProvider = useSwitchProvider()
  const deleteProvider = useDeleteProvider()
  const { data: envVars } = useQuery({ queryKey: ['settings-env'], queryFn: settingsApi.getEnv })
  const [showAdd, setShowAdd] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [showNvidiaWizard, setShowNvidiaWizard] = useState(false)

  const nvidiaKeyConfigured = !!(envVars?.['NVIDIA_NIM_API_KEY'])

  const handleActivate = (provider: Provider) => {
    if (provider.id === 'nvidia_nim' && !nvidiaKeyConfigured) {
      setShowNvidiaWizard(true)
      return
    }
    switchProvider.mutate(provider.id)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Proveedores</h2>
          <p className="text-sm text-gray-500 mt-0.5">Gestiona los backends de LLM disponibles</p>
        </div>
        <Button onClick={() => setShowAdd(true)}>+ Agregar proveedor</Button>
      </div>

      {switchProvider.isError && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          Error al cambiar proveedor:{' '}
          {(switchProvider.error as any)?.response?.data?.detail || (switchProvider.error as any)?.message || 'Error desconocido'}
        </div>
      )}

      {isLoading ? (
        <Spinner className="h-6 w-6 text-brand-500" />
      ) : (
        <div className="space-y-3">
          {registry?.providers.map((p) => {
            const isActive = registry.active_provider_id === p.id
            return (
              <Card key={p.id}>
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-gray-800">{p.name}</h3>
                      <Badge label={p.id} variant="neutral" />
                      {isActive && <Badge label="Activo" variant="info" />}
                    </div>
                    {p.description && (
                      <p className="text-sm text-gray-500 mt-0.5">{p.description}</p>
                    )}
                    <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-500">
                      <span><span className="text-gray-400">API base:</span> {p.api_base}</span>
                      <span><span className="text-gray-400">Prefijo:</span> {p.litellm_prefix}</span>
                      {p.auth_env_var && (
                        <span><span className="text-gray-400">Auth var:</span> {p.auth_env_var}</span>
                      )}
                      {p.active_model && (
                        <span><span className="text-gray-400">Modelo:</span> {p.active_model}</span>
                      )}
                      {p.models_endpoint && (
                        <span className="col-span-2"><span className="text-gray-400">Models endpoint:</span> {p.models_endpoint}</span>
                      )}
                      {p.rate_limit_rpm > 0 && (
                        <span><span className="text-gray-400">Rate limit:</span> {p.rate_limit_rpm} req/min</span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {!isActive && (
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={switchProvider.isPending && switchProvider.variables === p.id}
                        onClick={() => handleActivate(p)}
                      >
                        Activar
                      </Button>
                    )}
                    {!isActive && (
                      confirmDelete === p.id ? (
                        <div className="flex gap-1">
                          <Button
                            variant="danger"
                            size="sm"
                            loading={deleteProvider.isPending}
                            onClick={() => deleteProvider.mutate(p.id, { onSuccess: () => setConfirmDelete(null) })}
                          >
                            Confirmar
                          </Button>
                          <Button variant="secondary" size="sm" onClick={() => setConfirmDelete(null)}>
                            No
                          </Button>
                        </div>
                      ) : (
                        <Button variant="secondary" size="sm" onClick={() => setConfirmDelete(p.id)}>
                          Eliminar
                        </Button>
                      )
                    )}
                  </div>
                </div>
                {p.id === 'llamacpp' && <LlamaCppPanel provider={p} />}
              </Card>
            )
          })}
        </div>
      )}

      {registry && <RoutingPanel providers={registry.providers} />}

      {showAdd && <AddProviderModal onClose={() => setShowAdd(false)} />}
      {showNvidiaWizard && (
        <NvidiaWizard
          onClose={() => setShowNvidiaWizard(false)}
          onComplete={() => {
            setShowNvidiaWizard(false)
            switchProvider.mutate('nvidia_nim')
          }}
        />
      )}
    </div>
  )
}
