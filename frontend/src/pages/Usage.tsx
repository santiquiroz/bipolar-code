import { useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { useUsageSummary, useUsageHistory } from '@/hooks/useUsage'
import { useDecisions, useDecisionsSummary, useSmartHealth, useResetHealth } from '@/hooks/useSmart'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'
import { Badge } from '@/components/Badge'

const PERIOD_LABELS = { day: 'Hoy', week: 'Esta semana', month: 'Este mes' } as const
type Period = keyof typeof PERIOD_LABELS

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: '#8b5cf6',
  nvidia_nim: '#10b981',
  openrouter: '#f59e0b',
  deepseek: '#3b82f6',
  copilot: '#ec4899',
  lmstudio: '#6b7280',
  ollama: '#14b8a6',
}

function formatTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(0)}k`
  return String(n)
}

export function Usage() {
  const [period, setPeriod] = useState<Period>('day')
  const { data: summary, isLoading: summaryLoading } = useUsageSummary(period)
  const { data: history, isLoading: historyLoading } = useUsageHistory({ limit: 50 })
  const { data: decisionSummary } = useDecisionsSummary(period)
  const { data: decisions } = useDecisions({ limit: 100 })
  const { data: health } = useSmartHealth()
  const resetHealth = useResetHealth()

  const byProvider = summary?.by_provider || {}
  const series = summary?.series || []

  const chartData = (series as any[]).reduce((acc: any[], item: any) => {
    const existing = acc.find((d: any) => d.period === item.period)
    if (existing) {
      existing[item.provider_id] = item.cost || 0
    } else {
      acc.push({ period: item.period, [item.provider_id]: item.cost || 0 })
    }
    return acc
  }, [])

  const totalCost = Object.values(byProvider).reduce((s: number, p: any) => s + (p.cost_usd || 0), 0)
  const totalRequests = Object.values(byProvider).reduce((s: number, p: any) => s + (p.requests || 0), 0)
  const totalInput = Object.values(byProvider).reduce((s: number, p: any) => s + (p.input_tokens || 0), 0)
  const totalOutput = Object.values(byProvider).reduce((s: number, p: any) => s + (p.output_tokens || 0), 0)

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">Uso y Costos</h1>
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
          {(Object.keys(PERIOD_LABELS) as Period[]).map(p => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded text-sm font-medium transition-colors ${
                period === p ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {PERIOD_LABELS[p]}
            </button>
          ))}
        </div>

        <Card title="Decisiones de routing">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            {[
              ['Decisiones', decisionSummary?.count ?? 0],
              ['Acuerdo legacy ↔ smart', `${((decisionSummary?.agreement_rate ?? 0) * 100).toFixed(1)}%`],
              ['Ms promedio', `${(decisionSummary?.avg_decision_ms ?? 0).toFixed(1)} ms`],
              ['Resultados ok/error', `${decisionSummary?.outcomes.ok ?? 0} / ${decisionSummary?.outcomes.error ?? 0}`],
            ].map(([label, value]) => <div key={String(label)} className="bg-gray-50 rounded-lg p-3"><p className="text-xs text-gray-500">{label}</p><p className="font-bold text-gray-800 mt-1">{value}</p></div>)}
          </div>
          <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="text-gray-400 text-left border-b"><th>Hora</th><th>Superficie</th><th>Tier / score</th><th>Intent</th><th>Destino</th><th>Shadow</th><th>Origen</th><th>Resultado</th></tr></thead><tbody>{(decisions?.decisions || []).map(row => <tr key={row.id} className="border-b border-gray-50"><td className="py-2">{row.timestamp.slice(11, 16)}</td><td>{row.surface}</td><td><Badge label={`${row.tier} / ${row.score}`} /></td><td>{row.intent}</td><td>{row.chosen_key}</td><td>{row.would_key}</td><td>{row.source}</td><td>{row.outcome}</td></tr>)}</tbody></table></div>
        </Card>
        <Card title="Salud de destinos">
          <table className="w-full text-sm"><thead><tr className="text-gray-400 text-xs text-left border-b"><th>Destino</th><th>Estado</th><th>Fallos</th><th /></tr></thead><tbody>{Object.entries(health?.targets || {}).map(([target, item]) => <tr key={target} className="border-b border-gray-50"><td className="py-2">{target}</td><td><Badge label={item.state} variant={item.state === 'available' ? 'success' : 'warning'} /></td><td>{item.consecutive_failures ?? 0}</td><td><Button size="sm" variant="secondary" onClick={() => resetHealth.mutate(target)}>Reset</Button></td></tr>)}</tbody></table>
        </Card>
      </div>

      {/* Summary cards */}
      {summaryLoading ? (
        <div className="text-sm text-gray-400">Cargando...</div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { label: 'Total gastado', value: `$${totalCost.toFixed(4)}` },
            { label: 'Requests', value: totalRequests.toLocaleString() },
            { label: 'Tokens entrada', value: formatTokens(totalInput) },
            { label: 'Tokens salida', value: formatTokens(totalOutput) },
          ].map(card => (
            <div key={card.label} className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-xs">{card.label}</p>
              <p className="text-gray-800 text-xl font-bold mt-1">{card.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Chart */}
      {chartData.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h2 className="text-gray-700 font-medium mb-4 text-sm">Gasto por período</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData}>
              <XAxis dataKey="period" stroke="#9ca3af" tick={{ fontSize: 11 }} />
              <YAxis stroke="#9ca3af" tick={{ fontSize: 11 }} tickFormatter={v => `$${(v as number).toFixed(3)}`} />
              <Tooltip
                contentStyle={{ backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, fontSize: 12 }}
                formatter={(value) => [`$${(+(value ?? 0)).toFixed(4)}`, '']}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {Object.keys(PROVIDER_COLORS).map(pid =>
                chartData.some((d: any) => d[pid] !== undefined) ? (
                  <Bar key={pid} dataKey={pid} stackId="a" fill={PROVIDER_COLORS[pid]} name={pid} />
                ) : null
              )}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* By provider */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h2 className="text-gray-700 font-medium mb-4 text-sm">Por provider</h2>
        {Object.keys(byProvider).length === 0 ? (
          <p className="text-gray-400 text-sm">Sin datos aún — envía algunos mensajes.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-400 border-b border-gray-100 text-xs">
                <th className="text-left pb-2">Provider</th>
                <th className="text-right pb-2">Requests</th>
                <th className="text-right pb-2">Tokens</th>
                <th className="text-right pb-2">Costo</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(byProvider).map(([pid, data]: [string, any]) => (
                <tr key={pid} className="border-b border-gray-50 text-gray-600">
                  <td className="py-2">
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: PROVIDER_COLORS[pid] || '#6b7280' }} />
                      {pid}
                    </div>
                  </td>
                  <td className="py-2 text-right">{data.requests}</td>
                  <td className="py-2 text-right">{formatTokens((data.input_tokens || 0) + (data.output_tokens || 0))}</td>
                  <td className="py-2 text-right">
                    {data.cost_usd > 0
                      ? `$${data.cost_usd.toFixed(4)}`
                      : <span className="text-emerald-600 text-xs font-medium">GRATIS</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Recent requests */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h2 className="text-gray-700 font-medium mb-4 text-sm">Últimos requests</h2>
        {historyLoading ? (
          <p className="text-gray-400 text-sm">Cargando...</p>
        ) : (
          <div className="space-y-1">
            {((history as any[]) || []).slice(0, 20).map((req: any) => (
              <div key={req.id} className="flex items-center justify-between text-xs py-1.5 border-b border-gray-50">
                <span className="text-gray-400 w-14 shrink-0">{req.timestamp.slice(11, 16)}</span>
                <span className="text-gray-600 flex-1 truncate mx-2">{req.model}</span>
                <span className="text-gray-400 hidden sm:block">{formatTokens(req.input_tokens || 0)} / {formatTokens(req.output_tokens || 0)}</span>
                <span className="text-gray-500 w-20 text-right ml-2">
                  {req.cost_usd != null
                    ? `$${req.cost_usd.toFixed(5)}`
                    : <span className="text-emerald-600">GRATIS</span>}
                </span>
                {req.truncated ? <span className="text-yellow-500 ml-1">✂</span> : null}
              </div>
            ))}
            {(!history || (history as any[]).length === 0) && (
              <p className="text-gray-400 text-sm">Sin requests registrados aún.</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
