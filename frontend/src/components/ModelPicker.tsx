import { Spinner } from '@/components/Spinner'
import { useProviderModels } from '@/hooks/useProviders'
import { useSetProviderModel } from '@/hooks/useProviders'
import type { Provider } from '@/types/provider'

interface ModelPickerProps {
  provider: Provider
}

export function ModelPicker({ provider }: ModelPickerProps) {
  const { data, isLoading, error } = useProviderModels(provider.id, !!provider.models_endpoint)
  const setModel = useSetProviderModel()

  if (!provider.models_endpoint) {
    return (
      <div className="mt-4 space-y-2">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Modelo activo</p>
        <div className="flex items-center gap-2">
          <code className="text-sm bg-gray-100 px-2 py-1 rounded text-gray-700">{provider.active_model || 'no configurado'}</code>
        </div>
        <p className="text-xs text-gray-400">Este proveedor no tiene endpoint de modelos — configúralo manualmente.</p>
      </div>
    )
  }

  if (isLoading) return <Spinner className="h-4 w-4 text-brand-500 mt-3" />

  if (error || !data?.models.length) {
    return (
      <p className="mt-3 text-xs text-amber-600">
        {data?.note ?? 'No se pudieron cargar los modelos — verifica el token/API key.'}
      </p>
    )
  }

  return (
    <div className="mt-4 space-y-2">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
        Modelo activo — {data.models.length} disponibles
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-64 overflow-y-auto pr-1">
        {data.models.map((m) => {
          const isActive = provider.active_model === m.id
          const isLoading = setModel.isPending && setModel.variables?.model_id === m.id
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
              {isLoading && <Spinner className="absolute right-2 top-2 h-3 w-3 text-brand-500" />}
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-gray-800 truncate">{m.name || m.id}</p>
                {m.vendor && <p className="text-xs text-gray-400 truncate">{m.vendor}</p>}
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
