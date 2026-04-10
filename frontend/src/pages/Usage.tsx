import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Spinner } from '@/components/Spinner'
import { useAnthropicUsage, useLogStats } from '@/hooks/useUsage'

function TokenBar({ used, label }: { used: number; label: string }) {
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-500 mb-1">
        <span>{label}</span>
        <span>{used.toLocaleString()} tokens</span>
      </div>
      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-brand-500 rounded-full transition-all"
          style={{ width: `${Math.min((used / 1_000_000) * 100, 100)}%` }}
        />
      </div>
      <p className="text-xs text-gray-400 mt-0.5">of 1M shown</p>
    </div>
  )
}

export function Usage() {
  const anthropic = useAnthropicUsage()
  const logs = useLogStats()

  return (
    <div className="space-y-6">
      <Card title="Anthropic API Usage">
        {anthropic.isLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
          <div className="space-y-4">
            {anthropic.data?.map((u, i) => (
              <div key={i} className="p-4 rounded-xl bg-gray-50 space-y-3">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-sm text-gray-800">{u.model}</p>
                  <Badge label={u.backend} variant="info" />
                </div>
                {u.note ? (
                  <p className="text-xs text-gray-400">{u.note}</p>
                ) : (
                  <>
                    {u.input_tokens != null && <TokenBar used={u.input_tokens} label="Input tokens" />}
                    {u.output_tokens != null && <TokenBar used={u.output_tokens} label="Output tokens" />}
                    {u.estimated_cost_usd != null && (
                      <p className="text-sm font-semibold text-emerald-600">
                        Estimated cost: ${u.estimated_cost_usd.toFixed(6)}
                      </p>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>
        )}
        <Button variant="secondary" size="sm" className="mt-4" onClick={() => anthropic.refetch()}>
          Refresh
        </Button>
      </Card>

      <Card title="Usage from proxy logs">
        {logs.isLoading ? <Spinner className="h-5 w-5 text-brand-500" /> : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-400 border-b border-gray-100">
                <th className="pb-2">Model</th>
                <th className="pb-2">Requests</th>
                <th className="pb-2">Input tokens</th>
                <th className="pb-2">Output tokens</th>
                <th className="pb-2">Est. cost</th>
              </tr>
            </thead>
            <tbody>
              {logs.data?.map((u, i) => (
                <tr key={i} className="border-b border-gray-50 last:border-0">
                  <td className="py-2 font-medium text-gray-700">{u.model}</td>
                  <td className="py-2 text-gray-500">{u.requests_count}</td>
                  <td className="py-2 text-gray-500">{u.input_tokens?.toLocaleString() ?? '—'}</td>
                  <td className="py-2 text-gray-500">{u.output_tokens?.toLocaleString() ?? '—'}</td>
                  <td className="py-2 text-emerald-600 font-medium">
                    {u.estimated_cost_usd != null ? `$${u.estimated_cost_usd.toFixed(6)}` : '—'}
                  </td>
                </tr>
              ))}
              {logs.data?.length === 0 && (
                <tr><td colSpan={5} className="py-4 text-center text-gray-400">No log data found</td></tr>
              )}
            </tbody>
          </table>
        )}
        <Button variant="secondary" size="sm" className="mt-4" onClick={() => logs.refetch()}>
          Refresh
        </Button>
      </Card>
    </div>
  )
}
