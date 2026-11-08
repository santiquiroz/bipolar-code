import { useState } from 'react'
import { Card } from '@/components/Card'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { Icon } from '@/components/Icon'
import { useDelegationEfficiency } from '@/hooks/useDelegate'
import type { DelegationEfficiency as Efficiency, EfficiencyWarning } from '@/types/smart'

const DAYS = [7, 30, 90]
const POOL_COLORS: Record<string, string> = {
  anthropic_account: '#8b5cf6',
  anthropic_api: '#f43f5e',
  subscription: '#0ea5e9',
  prepaid: '#f59e0b',
  free: '#10b981',
}
const LEVELS = {
  high: { label: 'alto', variant: 'error' as const },
  medium: { label: 'medio', variant: 'warning' as const },
  info: { label: 'info', variant: 'info' as const },
}

const pct = (v: number | null) => (v == null ? '—' : `${v.toFixed(1)} %`)
const usd = (v: number | null, digits = 2) => (v == null ? '—' : `US$${v.toFixed(digits)}`)
const share = (n: number, total: number) => (total > 0 ? (n / total) * 100 : 0)

function DaysControl({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  return (
    <div className="inline-flex gap-1 bg-gray-100 rounded-lg p-1 mb-4" role="group" aria-label="Ventana en días">
      {DAYS.map(d => (
        <button
          key={d}
          type="button"
          aria-pressed={days === d}
          onClick={() => onChange(d)}
          className={`px-3 py-1 rounded text-sm font-medium transition-colors ${days === d ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
        >
          {d} días
        </button>
      ))}
    </div>
  )
}

function Warnings({ items }: { items: EfficiencyWarning[] }) {
  if (items.length === 0) return null
  return (
    <ul className="space-y-2 mb-4">
      {items.map((w, i) => (
        <li key={`${w.code}-${w.agent_id ?? i}`} className="flex items-start gap-2 text-sm text-gray-700">
          <Icon name={w.level === 'info' ? 'info' : 'alert'} className="shrink-0 mt-0.5" />
          <Badge label={LEVELS[w.level].label} variant={LEVELS[w.level].variant} />
          <span>{w.message}</span>
        </li>
      ))}
    </ul>
  )
}

function Kpi({ label, value, hint, warn }: { label: string; value: string; hint?: string; warn?: boolean }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`font-bold text-xl mt-1 tabular-nums ${warn ? 'text-amber-600' : 'text-gray-800'}`}>{value}</p>
      {hint && <p className="text-xs text-gray-400 mt-1">{hint}</p>}
    </div>
  )
}

function Kpis({ data }: { data: Efficiency }) {
  const t = data.totals
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
      <Kpi label="Jobs" value={String(t.jobs)} />
      <Kpi label="Éxito" value={pct(t.success_pct)} />
      <Kpi
        label="Hechos por Claude"
        value={pct(t.anthropic_share_pct)}
        hint="de los éxitos; no ahorran cupo de Anthropic"
        warn={t.anthropic_share_pct != null && t.anthropic_share_pct >= 50}
      />
      <Kpi label="Costo reportado" value={usd(t.cost_usd)} />
    </div>
  )
}

function PoolBar({ data }: { data: Efficiency }) {
  const total = data.pools.reduce((s, p) => s + p.succeeded, 0)
  if (total === 0) return null
  const summary = data.pools.filter(p => p.succeeded > 0).map(p => `${p.label} ${share(p.succeeded, total).toFixed(0)} %`).join(', ')
  return (
    <div className="mb-4">
      <div role="img" aria-label={`Éxitos por fondo: ${summary}`} className="flex h-3 w-full overflow-hidden rounded-full bg-gray-100">
        {data.pools.map(p => p.succeeded > 0 && (
          <div key={p.pool} style={{ width: `${share(p.succeeded, total)}%`, backgroundColor: POOL_COLORS[p.pool] ?? '#64748b' }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
        {data.pools.map(p => (
          <li key={p.pool} className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full" style={{ backgroundColor: POOL_COLORS[p.pool] ?? '#64748b' }} />
            {p.label}: {p.succeeded} éxitos ({share(p.succeeded, total).toFixed(1)} %)
          </li>
        ))}
      </ul>
    </div>
  )
}

function AgentsTable({ data }: { data: Efficiency }) {
  const poolLabel = (pool: string) => data.pools.find(p => p.pool === pool)?.label ?? pool
  const num = 'py-2 pl-3 text-right tabular-nums whitespace-nowrap'
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-400 border-b">
            <th className="text-left pb-2">Agente</th>
            <th className="text-left pb-2">Fondo</th>
            <th className="text-right pb-2 pl-3 whitespace-nowrap">Jobs</th>
            <th className="text-right pb-2 pl-3 whitespace-nowrap">Éxito %</th>
            <th className="text-right pb-2 pl-3 whitespace-nowrap">Aceptación %</th>
            <th className="text-right pb-2 pl-3 whitespace-nowrap">Costo</th>
            <th className="text-right pb-2 pl-3 whitespace-nowrap">Costo por éxito</th>
            <th className="text-left pb-2 pl-4">Modelos</th>
          </tr>
        </thead>
        <tbody>
          {data.agents.map(a => (
            <tr key={a.agent_id} className="border-b border-gray-50 text-gray-600">
              <td className="py-2 font-medium text-gray-800">{a.name}</td>
              <td className="py-2">{poolLabel(a.pool)}</td>
              <td className={num}>{a.jobs}</td>
              <td className={num}>{pct(a.success_pct)}</td>
              <td className={num}>{pct(a.accept_pct)}</td>
              <td className={num}>{usd(a.cost_usd)}</td>
              <td className={num}>{usd(a.cost_per_success, 3)}</td>
              <td className="py-2 pl-4">{a.models.join(', ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function DelegationEfficiency() {
  const [days, setDays] = useState(30)
  const { data, isLoading, isError, refetch } = useDelegationEfficiency(days)

  let body
  if (isLoading) {
    body = (
      <div className="space-y-3" aria-label="Cargando eficiencia">
        {[0, 1, 2].map(i => <div key={i} className="h-12 rounded-lg bg-gray-100 animate-pulse" />)}
      </div>
    )
  } else if (isError || !data) {
    body = (
      <div className="flex items-center gap-3">
        <p className="text-sm text-red-600">No se pudo cargar la eficiencia</p>
        <Button size="sm" variant="secondary" onClick={() => refetch()}>Reintentar</Button>
      </div>
    )
  } else if (data.totals.jobs === 0) {
    body = <p className="text-sm text-gray-500">Todavía no hay jobs en esta ventana. Delega una tarea desde Claude Code con el MCP de bipolar o desde la página Agentes.</p>
  } else {
    body = (
      <>
        <Warnings items={data.warnings} />
        <Kpis data={data} />
        <PoolBar data={data} />
        <AgentsTable data={data} />
      </>
    )
  }

  return (
    <Card title="Eficiencia de la delegación">
      <DaysControl days={days} onChange={setDays} />
      {body}
    </Card>
  )
}
