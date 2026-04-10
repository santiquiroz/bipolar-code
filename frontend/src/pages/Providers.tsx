import { useState } from 'react'
import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Spinner } from '@/components/Spinner'
import { AddProviderModal } from '@/components/AddProviderModal'
import { useProviders, useSwitchProvider, useDeleteProvider } from '@/hooks/useProviders'

export function Providers() {
  const { data: registry, isLoading } = useProviders()
  const switchProvider = useSwitchProvider()
  const deleteProvider = useDeleteProvider()
  const [showAdd, setShowAdd] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Proveedores</h2>
          <p className="text-sm text-gray-500 mt-0.5">Gestiona los backends de LLM disponibles</p>
        </div>
        <Button onClick={() => setShowAdd(true)}>+ Agregar proveedor</Button>
      </div>

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
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {!isActive && (
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={switchProvider.isPending && switchProvider.variables === p.id}
                        onClick={() => switchProvider.mutate(p.id)}
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
              </Card>
            )
          })}
        </div>
      )}

      {showAdd && <AddProviderModal onClose={() => setShowAdd(false)} />}
    </div>
  )
}
