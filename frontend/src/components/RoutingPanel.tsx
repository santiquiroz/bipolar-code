import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'
import { routingApi } from '@/services/api'
import type { Provider, RoutingRule } from '@/types/provider'

interface RoutingPanelProps {
  providers: Provider[]
}

const EMPTY_RULE: RoutingRule = { pattern: '', min_tokens: 0, provider_id: '', model: '' }

export function RoutingPanel({ providers }: RoutingPanelProps) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['routing'], queryFn: routingApi.get })
  const [enabled, setEnabled] = useState(false)
  const [rules, setRules] = useState<RoutingRule[]>([])
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (data && !dirty) {
      setEnabled(data.enabled)
      setRules(data.rules)
    }
  }, [data, dirty])

  const save = useMutation({
    mutationFn: () => routingApi.set({ enabled, rules: rules.filter(r => r.provider_id) }),
    onSuccess: () => {
      setDirty(false)
      qc.invalidateQueries({ queryKey: ['routing'] })
    },
  })

  const updateRule = (i: number, patch: Partial<RoutingRule>) => {
    setRules(rules.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
    setDirty(true)
  }

  return (
    <Card title="Routing por escenario">
      <p className="text-xs text-gray-400 mb-3">
        Enruta cada request según el modelo pedido por Claude Code: primer match gana.
        Ej: patrón <code className="bg-gray-100 px-1 rounded">haiku</code> (tareas background) → modelo
        chico local; <code className="bg-gray-100 px-1 rounded">opus</code> → Anthropic real; min tokens
        60000 → provider con contexto largo. Sin match → provider activo.
      </p>

      <label className="flex items-center gap-2 text-sm text-gray-600 mb-3">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => { setEnabled(e.target.checked); setDirty(true) }}
        />
        Activar routing
      </label>

      <div className="space-y-2">
        {rules.map((rule, i) => (
          <div key={i} className="grid grid-cols-[1fr_90px_1fr_1fr_28px] gap-2 items-center">
            <input
              value={rule.pattern}
              onChange={(e) => updateRule(i, { pattern: e.target.value })}
              placeholder="patrón (ej: haiku)"
              className="text-xs border border-gray-300 rounded-lg px-2 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-400"
            />
            <input
              type="number"
              value={rule.min_tokens}
              onChange={(e) => updateRule(i, { min_tokens: Number(e.target.value) })}
              title="Min tokens (longContext); 0 = sin umbral"
              className="text-xs border border-gray-300 rounded-lg px-2 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-400"
            />
            <select
              value={rule.provider_id}
              onChange={(e) => updateRule(i, { provider_id: e.target.value })}
              className="text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-400"
            >
              <option value="">— provider —</option>
              {providers.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <input
              value={rule.model}
              onChange={(e) => updateRule(i, { model: e.target.value })}
              placeholder="modelo (vacío = el activo)"
              className="text-xs border border-gray-300 rounded-lg px-2 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-400"
            />
            <button
              onClick={() => { setRules(rules.filter((_, idx) => idx !== i)); setDirty(true) }}
              className="text-red-500 hover:text-red-700 text-sm"
              title="Eliminar regla"
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="flex gap-2 mt-3">
        <Button variant="secondary" size="sm" onClick={() => { setRules([...rules, { ...EMPTY_RULE }]); setDirty(true) }}>
          + Regla
        </Button>
        {dirty && (
          <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>
            Guardar
          </Button>
        )}
      </div>
      {save.isError && (
        <p className="text-xs text-red-600 mt-2">
          {(save.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Error al guardar'}
        </p>
      )}
    </Card>
  )
}
