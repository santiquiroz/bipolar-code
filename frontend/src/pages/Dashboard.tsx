import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Spinner } from '@/components/Spinner'
import { useProxyStatus, useSwitchBackend } from '@/hooks/useProxy'
import { useActiveModels } from '@/hooks/useModels'
import { useLogStats } from '@/hooks/useUsage'
import type { BackendType } from '@/types'

const BACKENDS: { id: BackendType; label: string; desc: string }[] = [
  { id: 'copilot', label: 'GitHub Copilot', desc: 'Business API' },
  { id: 'claude',  label: 'Anthropic Direct', desc: 'Claude API' },
  { id: 'gemma',   label: 'LM Studio', desc: 'Local Gemma 4 26B' },
]

export function Dashboard() {
  const { data: status, isLoading: statusLoading } = useProxyStatus()
  const { data: models = [] } = useActiveModels()
  const { data: logStats = [] } = useLogStats()
  const switchMut = useSwitchBackend()

  const totalRequests = logStats.reduce((s, r) => s + r.requests_count, 0)
  const totalCost = logStats.reduce((s, r) => s + (r.estimated_cost_usd ?? 0), 0)

  return (
    <div className="space-y-6">
      {/* Proxy status */}
      <Card title="Proxy Status">
        {statusLoading ? (
          <Spinner className="h-6 w-6 text-brand-500" />
        ) : status ? (
          <div className="flex flex-wrap items-center gap-4">
            <Badge label={status.running ? 'Running' : 'Stopped'} variant={status.running ? 'success' : 'error'} />
            <span className="text-sm text-gray-500">Port <strong>{status.port}</strong></span>
            <span className="text-sm text-gray-500">Config <code className="text-xs bg-gray-100 px-1 rounded">{status.config_file}</code></span>
            <span className="text-sm text-gray-500">
              <Badge label={`${status.healthy_models} healthy`} variant="success" />
              {status.unhealthy_models > 0 && (
                <Badge label={`${status.unhealthy_models} unhealthy`} variant="error" />
              )}
            </span>
          </div>
        ) : (
          <p className="text-sm text-red-500">Proxy unreachable</p>
        )}
      </Card>

      {/* Backend switcher */}
      <Card title="Active Backend">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {BACKENDS.map((b) => {
            const isActive = status?.active_backend === b.id
            const isLoading = switchMut.isPending && switchMut.variables === b.id
            return (
              <button
                key={b.id}
                onClick={() => switchMut.mutate(b.id)}
                disabled={isActive || switchMut.isPending}
                className={`relative p-4 rounded-xl border-2 text-left transition-all
                  ${isActive
                    ? 'border-brand-600 bg-brand-50'
                    : 'border-gray-200 hover:border-brand-300 bg-white'
                  } disabled:opacity-60`}
              >
                {isLoading && <Spinner className="absolute top-3 right-3 h-4 w-4 text-brand-500" />}
                <p className="font-semibold text-sm text-gray-800">{b.label}</p>
                <p className="text-xs text-gray-500 mt-0.5">{b.desc}</p>
                {isActive && <Badge label="Active" variant="info" />}
              </button>
            )
          })}
        </div>
        {switchMut.isError && (
          <p className="mt-3 text-sm text-red-600">Switch failed — check proxy logs</p>
        )}
        {switchMut.isSuccess && (
          <p className="mt-3 text-sm text-emerald-600">Switched — reloading status in 3s…</p>
        )}
      </Card>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card>
          <p className="text-3xl font-bold text-gray-800">{models.length}</p>
          <p className="text-sm text-gray-500 mt-1">Active Models</p>
        </Card>
        <Card>
          <p className="text-3xl font-bold text-gray-800">{totalRequests}</p>
          <p className="text-sm text-gray-500 mt-1">Total Requests (log)</p>
        </Card>
        <Card>
          <p className="text-3xl font-bold text-gray-800">${totalCost.toFixed(4)}</p>
          <p className="text-sm text-gray-500 mt-1">Estimated Cost (USD)</p>
        </Card>
      </div>
    </div>
  )
}
