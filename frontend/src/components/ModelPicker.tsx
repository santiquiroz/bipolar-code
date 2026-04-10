import { Spinner } from '@/components/Spinner'
import { Button } from '@/components/Button'
import { useProviderModels, useSetProviderModel, useRefreshToken } from '@/hooks/useProviders'
import type { Provider } from '@/types/provider'

interface ModelPickerProps {
  provider: Provider
}

function errorMessage(error: unknown): { msg: string; isAuth: boolean } {
  const status = (error as { response?: { status?: number } })?.response?.status
  if (status === 401 || status === 403) {
    return { msg: 'Token expirado o inválido', isAuth: true }
  }
  if (status === 503) {
    return { msg: 'No se pudo conectar al endpoint de modelos', isAuth: false }
  }
  return { msg: 'Error al cargar modelos', isAuth: false }
}

export function ModelPicker({ provider }: ModelPickerProps) {
  const { data, isLoading, error, refetch } = useProviderModels(provider.id, !!provider.models_endpoint)
  const setModel = useSetProviderModel()
  const refreshToken = useRefreshToken()

  if (!provider.models_endpoint) {
    return (
      <div className="space-y-1.5">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Modelo activo</p>
        <code className="text-sm bg-gray-100 px-2 py-1 rounded text-gray-700 block">
          {provider.active_model || 'no configurado'}
        </code>
        <p className="text-xs text-gray-400">Sin endpoint de modelos — configúralo en Settings.</p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 mt-1">
        <Spinner className="h-4 w-4 text-brand-500" />
        <span className="text-xs text-gray-400">Cargando modelos…</span>
      </div>
    )
  }

  if (error) {
    const { msg, isAuth } = errorMessage(error)
    return (
      <div className="space-y-2 mt-1">
        <p className="text-xs text-red-500">{msg}</p>
        <div className="flex gap-2 flex-wrap">
          {isAuth && (
            <Button
              variant="secondary"
              size="sm"
              loading={refreshToken.isPending}
              onClick={() => refreshToken.mutate(provider.id, { onSuccess: () => refetch() })}
            >
              Refrescar token
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={() => refetch()}>
            Reintentar
          </Button>
        </div>
        {refreshToken.isSuccess && (
          <p className="text-xs text-emerald-600">
            {refreshToken.data?.refreshed
              ? `Token actualizado (${refreshToken.data.token_length} chars)`
              : refreshToken.data?.note}
          </p>
        )}
      </div>
    )
  }

  if (!data?.models.length) {
    return (
      <div className="space-y-1 mt-1">
        <p className="text-xs text-amber-600">{data?.note ?? 'Sin modelos disponibles'}</p>
        <Button variant="secondary" size="sm" onClick={() => refetch()}>Reintentar</Button>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Modelo activo — {data.models.length} disponibles
        </p>
        <button onClick={() => refetch()} className="text-xs text-gray-400 hover:text-brand-500 transition-colors">
          ↻ actualizar
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-56 overflow-y-auto pr-1">
        {data.models.map((m) => {
          const isActive = provider.active_model === m.id
          const isChanging = setModel.isPending && setModel.variables?.model_id === m.id
          return (
            <button
              key={m.id}
              onClick={() => !isActive && setModel.mutate({ provider_id: provider.id, model_id: m.id })}
              disabled={isActive || setModel.isPending}
              className={`relative flex items-center gap-2 p-2.5 rounded-lg border text-left transition-all
                ${isActive
                  ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-400'
                  : 'border-gray-200 hover:border-brand-300 bg-white hover:bg-gray-50'
                } disabled:cursor-not-allowed`}
            >
              {isChanging && <Spinner className="absolute right-2 top-2 h-3 w-3 text-brand-500" />}
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-gray-800 truncate">{m.name || m.id}</p>
                {m.vendor && <p className="text-xs text-gray-400">{m.vendor}</p>}
              </div>
              {isActive && <span className="shrink-0 w-2 h-2 rounded-full bg-brand-500" />}
            </button>
          )
        })}
      </div>
      {setModel.isSuccess && (
        <p className="text-xs text-emerald-600">Modelo actualizado.</p>
      )}
    </div>
  )
}
