import { Spinner } from '@/components/Spinner'
import { useCopilotModels } from '@/hooks/useModels'
import { useCopilotActiveModel, useSetCopilotModel } from '@/hooks/useSettings'

export function CopilotModelPicker() {
  const { data: models = [], isLoading: loadingModels } = useCopilotModels()
  const { data: activeData, isLoading: loadingActive } = useCopilotActiveModel()
  const setModel = useSetCopilotModel()

  const activeModel = activeData?.model ?? ''

  if (loadingModels || loadingActive) {
    return <Spinner className="h-4 w-4 text-brand-500" />
  }

  if (models.length === 0) {
    return <p className="text-xs text-amber-600">No se pudieron cargar los modelos — token puede estar expirado</p>
  }

  return (
    <div className="mt-4 space-y-2">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Modelo activo en Copilot</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {models.map((m) => {
          const isActive = activeModel === m.id
          const isLoading = setModel.isPending && setModel.variables === m.id
          return (
            <button
              key={m.id}
              onClick={() => !isActive && setModel.mutate(m.id)}
              disabled={isActive || setModel.isPending}
              className={`relative flex items-center gap-3 p-3 rounded-lg border text-left transition-all
                ${isActive
                  ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-400'
                  : 'border-gray-200 hover:border-brand-300 bg-white hover:bg-gray-50'
                } disabled:cursor-not-allowed`}
            >
              {isLoading && (
                <Spinner className="absolute right-2 top-2 h-3.5 w-3.5 text-brand-500" />
              )}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-800 truncate">{m.name}</p>
                <p className="text-xs text-gray-400 truncate">{m.id}</p>
                {m.vendor && (
                  <p className="text-xs text-gray-400">{m.vendor}</p>
                )}
              </div>
              {isActive && (
                <span className="shrink-0 w-2 h-2 rounded-full bg-brand-500" />
              )}
            </button>
          )
        })}
      </div>
      {setModel.isSuccess && !setModel.isPending && (
        <p className="text-xs text-emerald-600">Modelo cambiado — proxy reiniciando…</p>
      )}
      {setModel.isError && (
        <p className="text-xs text-red-600">Error al cambiar modelo</p>
      )}
    </div>
  )
}
