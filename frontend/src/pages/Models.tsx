import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Spinner } from '@/components/Spinner'
import { useActiveModels } from '@/hooks/useModels'

export function Models() {
  const active = useActiveModels()

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">Modelos activos</h2>
        <p className="text-sm text-gray-500 mt-0.5">Modelos expuestos actualmente por el proxy</p>
      </div>

      <Card>
        {active.isLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
          <div className="space-y-2">
            {active.data?.map((m, i) => (
              <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-gray-50">
                <div>
                  <p className="text-sm font-medium text-gray-800">{m.model_name}</p>
                  <p className="text-xs text-gray-400">{m.api_base}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge label={m.provider_id} variant="info" />
                  <Badge label={m.is_healthy ? 'healthy' : 'unhealthy'} variant={m.is_healthy ? 'success' : 'error'} />
                </div>
              </div>
            ))}
            {active.data?.length === 0 && (
              <p className="text-sm text-gray-400">No se encontraron modelos — ¿el proxy está corriendo?</p>
            )}
          </div>
        )}
      </Card>

      <p className="text-xs text-gray-400">
        Para ver los modelos disponibles por proveedor, ve a <strong>Providers</strong> y haz click en el proveedor activo.
      </p>
    </div>
  )
}
