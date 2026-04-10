import { useState } from 'react'
import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Spinner } from '@/components/Spinner'
import { ModelPicker } from '@/components/ModelPicker'
import { useProxyStatus, useStartProxy } from '@/hooks/useProxy'
import { useProviders, useSwitchProvider } from '@/hooks/useProviders'
import { useLogStats } from '@/hooks/useUsage'

export function Dashboard() {
  const { data: status, isLoading: statusLoading } = useProxyStatus()
  const { data: registry, isLoading: providersLoading } = useProviders()
  const { data: logStats = [] } = useLogStats()
  const switchProvider = useSwitchProvider()
  const startProxy = useStartProxy()
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null)

  const totalRequests = logStats.reduce((s, r) => s + r.requests_count, 0)
  const totalCost = logStats.reduce((s, r) => s + (r.estimated_cost_usd ?? 0), 0)
  const activeProvider = registry?.providers.find(p => p.id === registry.active_provider_id)

  return (
    <div className="space-y-6">
      {/* Proxy status */}
      <Card title="Proxy Status">
        {statusLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
          <div className="flex flex-wrap items-center gap-3">
            <Badge
              label={status?.running ? 'Running' : 'Stopped'}
              variant={status?.running ? 'success' : 'error'}
            />
            <span className="text-sm text-gray-500">Puerto <strong>{status?.port ?? 4001}</strong></span>
            {status?.running && (
              <Badge label={`${status.healthy_models} healthy`} variant="success" />
            )}
            {status?.unhealthy_models ? (
              <Badge label={`${status.unhealthy_models} unhealthy`} variant="error" />
            ) : null}
            {activeProvider && (
              <span className="text-sm text-gray-500">
                Proveedor: <strong>{activeProvider.name}</strong>
              </span>
            )}
            {!status?.running && (
              <Button
                size="sm"
                loading={startProxy.isPending}
                onClick={() => startProxy.mutate()}
              >
                Iniciar proxy
              </Button>
            )}
            {status?.running && (
              <Button
                variant="secondary"
                size="sm"
                loading={startProxy.isPending}
                onClick={() => startProxy.mutate()}
              >
                Reiniciar
              </Button>
            )}
            {startProxy.isSuccess && (
              <span className="text-xs text-emerald-600">Iniciando… (~5s)</span>
            )}
            {startProxy.isError && (
              <span className="text-xs text-red-500">Error al iniciar proxy</span>
            )}
          </div>
        )}
      </Card>

      {/* Provider switcher */}
      <Card title="Proveedor activo">
        {providersLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {registry?.providers.map((p) => {
                const isActive = registry.active_provider_id === p.id
                const isSwitching = switchProvider.isPending && switchProvider.variables === p.id
                const isExpanded = expandedProvider === p.id
                return (
                  <div
                    key={p.id}
                    className={`rounded-xl border-2 transition-all
                      ${isActive ? 'border-brand-600 bg-brand-50' : 'border-gray-200 bg-white'}`}
                  >
                    <button
                      className="w-full p-4 text-left"
                      onClick={() => {
                        if (!isActive) switchProvider.mutate(p.id)
                        else setExpandedProvider(isExpanded ? null : p.id)
                      }}
                      disabled={isSwitching}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="font-semibold text-sm text-gray-800">{p.name}</p>
                          <p className="text-xs text-gray-400 mt-0.5 truncate">{p.api_base}</p>
                          {p.description && (
                            <p className="text-xs text-gray-500 mt-1">{p.description}</p>
                          )}
                        </div>
                        {isSwitching && <Spinner className="h-4 w-4 text-brand-500 shrink-0" />}
                        {isActive && !isSwitching && (
                          <Badge label="Activo" variant="info" />
                        )}
                      </div>
                      {isActive && p.active_model && (
                        <p className="text-xs text-brand-600 font-medium mt-2 truncate">
                          {p.active_model}
                        </p>
                      )}
                    </button>

                    {/* Expandir para cambiar modelo — solo proveedor activo */}
                    {isActive && (
                      <div className="px-4 pb-4 border-t border-brand-100 pt-3">
                        <ModelPicker provider={p} />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {switchProvider.isError && (
              <p className="mt-3 text-sm text-red-600">Switch fallido — revisa los logs del proxy</p>
            )}
            {switchProvider.isSuccess && (
              <p className="mt-3 text-sm text-emerald-600">Cambiado — recargando en 3s…</p>
            )}
          </>
        )}
      </Card>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card>
          <p className="text-3xl font-bold text-gray-800">{registry?.providers.length ?? 0}</p>
          <p className="text-sm text-gray-500 mt-1">Proveedores configurados</p>
        </Card>
        <Card>
          <p className="text-3xl font-bold text-gray-800">{totalRequests}</p>
          <p className="text-sm text-gray-500 mt-1">Requests totales (log)</p>
        </Card>
        <Card>
          <p className="text-3xl font-bold text-gray-800">${totalCost.toFixed(4)}</p>
          <p className="text-sm text-gray-500 mt-1">Costo estimado (USD)</p>
        </Card>
      </div>
    </div>
  )
}
