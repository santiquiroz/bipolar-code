import { useState } from 'react'
import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Spinner } from '@/components/Spinner'
import { useActiveModels, useCopilotModels, useGitHubModels } from '@/hooks/useModels'

type Tab = 'active' | 'copilot' | 'github'

export function Models() {
  const [tab, setTab] = useState<Tab>('active')
  const active  = useActiveModels()
  const copilot = useCopilotModels()
  const github  = useGitHubModels()

  const tabs: { id: Tab; label: string }[] = [
    { id: 'active',  label: 'Active on Proxy' },
    { id: 'copilot', label: 'GitHub Copilot' },
    { id: 'github',  label: 'GitHub Models' },
  ]

  return (
    <div className="space-y-4">
      <div className="flex gap-2 border-b border-gray-200 pb-2">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors
              ${tab === t.id ? 'text-brand-600 border-b-2 border-brand-600' : 'text-gray-500 hover:text-gray-700'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'active' && (
        <Card title="Models running on proxy">
          {active.isLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
            <div className="space-y-2">
              {active.data?.map((m, i) => (
                <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-gray-50">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{m.model_name}</p>
                    <p className="text-xs text-gray-400">{m.api_base}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge label={m.backend} variant="info" />
                    <Badge label={m.is_healthy ? 'healthy' : 'unhealthy'} variant={m.is_healthy ? 'success' : 'error'} />
                  </div>
                </div>
              ))}
              {active.data?.length === 0 && <p className="text-sm text-gray-400">No models found</p>}
            </div>
          )}
        </Card>
      )}

      {tab === 'copilot' && (
        <Card title="Available Copilot Models">
          {copilot.isLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
            <div className="space-y-2">
              {copilot.data?.map((m) => (
                <div key={m.id} className="flex items-center justify-between p-3 rounded-lg bg-gray-50">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{m.name}</p>
                    <p className="text-xs text-gray-400">{m.id}</p>
                  </div>
                  {m.vendor && <Badge label={m.vendor} variant="neutral" />}
                </div>
              ))}
              {copilot.data?.length === 0 && (
                <p className="text-sm text-gray-400">No models returned — token may be expired</p>
              )}
            </div>
          )}
          <Button variant="secondary" size="sm" className="mt-4" onClick={() => copilot.refetch()}>
            Refresh
          </Button>
        </Card>
      )}

      {tab === 'github' && (
        <Card title="GitHub Models (inference.ai.azure.com)">
          {github.isLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
            <div className="space-y-2">
              {github.data?.map((m) => (
                <div key={m.id} className="flex items-center justify-between p-3 rounded-lg bg-gray-50">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{m.name}</p>
                    <p className="text-xs text-gray-400">{m.id}</p>
                  </div>
                  {m.vendor && <Badge label={m.vendor} variant="neutral" />}
                </div>
              ))}
              {github.data?.length === 0 && (
                <p className="text-sm text-gray-400">No models returned — check GITHUB_TOKEN</p>
              )}
            </div>
          )}
          <Button variant="secondary" size="sm" className="mt-4" onClick={() => github.refetch()}>
            Refresh
          </Button>
        </Card>
      )}
    </div>
  )
}
