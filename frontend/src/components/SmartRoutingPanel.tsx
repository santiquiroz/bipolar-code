import { useEffect, useState } from 'react'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'
import { Badge } from '@/components/Badge'
import { providersApi, smartApi } from '@/services/api'
import { useExplain, useSaveSmartConfig, useSmartConfig } from '@/hooks/useSmart'
import type { Provider } from '@/types/provider'
import type { SmartRoutingConfig, Tier } from '@/types/smart'

const TIERS: Tier[] = ['trivial', 'simple', 'standard', 'complex']
const blank: SmartRoutingConfig = {
  enabled: false, mode: 'shadow', thresholds: { simple: 25, standard: 50, complex: 75 }, tiers: [],
  budgets: [], honor_tier_header: true, sticky_tool_loops: true, sticky_ttl_seconds: 1800,
  skip_cooling_providers: true, respect_capabilities: true,
}
const inputClass = 'text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-400'

function normalized(value: SmartRoutingConfig): SmartRoutingConfig {
  return { ...blank, ...value, thresholds: { ...blank.thresholds, ...value.thresholds },
    tiers: TIERS.map(tier => value.tiers.find(p => p.tier === tier) || { tier, targets: [] }) }
}

export function SmartRoutingPanel({ providers }: { providers: Provider[] }) {
  const { data, isLoading } = useSmartConfig()
  const save = useSaveSmartConfig()
  const explain = useExplain()
  const [config, setConfig] = useState<SmartRoutingConfig>(blank)
  const [dirty, setDirty] = useState(false)
  const [taskModel, setTaskModel] = useState('')
  const [taskBody, setTaskBody] = useState('')
  const [tools, setTools] = useState(false)
  const [recommendationError, setRecommendationError] = useState('')
  const providerIds = providers.map(provider => provider.id).join(', ')

  useEffect(() => {
    if (data && !dirty) {
      const next = normalized(data.smart)
      if (JSON.stringify(next) !== JSON.stringify(config)) setConfig(next)
    }
  }, [data, dirty, config])
  const change = (patch: Partial<SmartRoutingConfig>) => { setConfig(c => ({ ...c, ...patch })); setDirty(true) }
  const policy = (tier: Tier) => config.tiers.find(p => p.tier === tier) || { tier, targets: [] }
  const setPolicy = (tier: Tier, csv: string) => {
    const targets = csv.split(',').map(s => s.trim()).filter(Boolean).map(item => {
      const [provider_id, ...model] = item.split(':')
      return { provider_id, model: model.join(':') }
    })
    change({ tiers: config.tiers.map(p => p.tier === tier ? { tier, targets } : p).concat(config.tiers.some(p => p.tier === tier) ? [] : [{ tier, targets }]) })
  }
  const suggest = async () => {
    const result = await smartApi.presetDefault()
    change({ tiers: result.tiers })
  }
  const saveConfig = () => save.mutate({ smart: config }, { onSuccess: () => setDirty(false) })
  const errorDetail = (error: unknown) => (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Error al guardar'

  return (
    <Card title="Gestión inteligente">
      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 font-medium text-gray-700">
          <input type="checkbox" checked={config.enabled} onChange={e => change({ enabled: e.target.checked })} />
          Routing inteligente (por complejidad y cuota)
        </label>
        <div className="flex rounded-lg bg-gray-100 p-1 text-xs">
          {(['shadow', 'active'] as const).map(mode => <button key={mode} onClick={() => change({ mode })}
            className={`px-3 py-1 rounded ${config.mode === mode ? 'bg-white shadow text-gray-800' : 'text-gray-500'}`}>
            {mode === 'shadow' ? 'Shadow' : 'Activo'}
          </button>)}
        </div>
      </div>
      <p className="text-xs text-gray-400 mt-2">Shadow registra qué haría sin cambiar el destino.</p>
      {isLoading ? <p className="text-sm text-gray-400 mt-4">Cargando...</p> : <>
        <div className="grid grid-cols-3 gap-2 mt-4">
          {(['simple', 'standard', 'complex'] as const).map(t => <label key={t} className="text-xs text-gray-500">{t}
            <input type="number" value={config.thresholds[t] ?? 0} onChange={e => change({ thresholds: { ...config.thresholds, [t]: Number(e.target.value) } })} className={`${inputClass} w-full mt-1`} />
          </label>)}
        </div>
        <div className="space-y-2 mt-4">
          {TIERS.map(tier => <div key={tier} className="flex items-center gap-2">
            <Badge label={tier} variant="neutral" />
            <input className={`${inputClass} flex-1 font-mono`} value={policy(tier).targets.map(t => `${t.provider_id}${t.model ? `:${t.model}` : ''}`).join(', ')}
              onChange={e => setPolicy(tier, e.target.value)} placeholder={`provider_id[:model], ... (${providerIds})`} />
            <Button size="sm" variant="secondary" onClick={suggest}>Sugerir</Button>
          </div>)}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-2 mt-4 text-xs text-gray-600">
          {(['honor_tier_header', 'sticky_tool_loops', 'skip_cooling_providers', 'respect_capabilities'] as const).map(key =>
            <label key={key}><input type="checkbox" checked={config[key]} onChange={e => change({ [key]: e.target.checked })} /> {key.replace(/_/g, ' ')}</label>)}
        </div>
        <div className="mt-4">
          <div className="flex justify-between items-center mb-2"><span className="text-xs font-semibold text-gray-600">Presupuestos</span>
            <Button size="sm" variant="secondary" onClick={() => change({ budgets: [...config.budgets, { target_key: '', window: 'day', max_requests: 0, max_cost_usd: 0 }] })}>+ añadir</Button></div>
          {config.budgets.map((budget, i) => <div key={i} className="grid grid-cols-[1fr_90px_90px_90px_28px] gap-2 mb-2">
            <input className={inputClass} value={budget.target_key} placeholder="provider:id o cli:id" onChange={e => change({ budgets: config.budgets.map((b, n) => n === i ? { ...b, target_key: e.target.value } : b) })} />
            <select className={inputClass} value={budget.window} onChange={e => change({ budgets: config.budgets.map((b, n) => n === i ? { ...b, window: e.target.value as typeof b.window } : b) })}><option>day</option><option>week</option><option>month</option></select>
            <input className={inputClass} type="number" value={budget.max_requests} placeholder="requests" onChange={e => change({ budgets: config.budgets.map((b, n) => n === i ? { ...b, max_requests: Number(e.target.value) } : b) })} />
            <input className={inputClass} type="number" value={budget.max_cost_usd} placeholder="USD" onChange={e => change({ budgets: config.budgets.map((b, n) => n === i ? { ...b, max_cost_usd: Number(e.target.value) } : b) })} />
            <button className="text-red-500" onClick={() => change({ budgets: config.budgets.filter((_, n) => n !== i) })}>✕</button>
          </div>)}
        </div>
        <div className="border-t mt-5 pt-4">
          <h4 className="text-sm font-semibold text-gray-700">¿A dónde iría?</h4>
          <div className="flex gap-2 mt-2"><input className={`${inputClass} flex-1`} value={taskModel} onChange={e => setTaskModel(e.target.value)} placeholder="Modelo solicitado" />
            <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={tools} onChange={e => setTools(e.target.checked)} /> herramientas</label></div>
          <textarea className={`${inputClass} w-full mt-2`} rows={3} value={taskBody} onChange={e => setTaskBody(e.target.value)} placeholder="Prompt o JSON del request" />
          <Button size="sm" className="mt-2" loading={explain.isPending} onClick={() => explain.mutate({ body: { model: taskModel, messages: [{ role: 'user', content: taskBody }], ...(tools ? { tools: [{}] } : {}) } })}>Probar ruta</Button>
          {explain.data && <div className="mt-3 text-xs space-y-1"><div><Badge label={`${explain.data.tier} · ${explain.data.score}`} variant="info" /> <span className="ml-2">{explain.data.intent}</span></div><div>Destino: <b>{explain.data.chosen_key}</b> {explain.data.chosen_model}</div><div>Shadow: <b>{explain.data.would_key}</b> {explain.data.would_model}</div><ul className="list-disc ml-5">{explain.data.reasons.map(r => <li key={r}>{r}</li>)}</ul>{explain.data.rejected.map(([key, reason]) => <div key={key} className="text-gray-400">Descartado {key}: {reason}</div>)}</div>}
        </div>
        {data?.recommendations.map(rec => <div key={rec.code} className="mt-3 rounded-lg bg-blue-50 border border-blue-100 p-3 text-xs text-blue-800 flex justify-between gap-3"><span>{rec.message}</span>{rec.patch && <Button size="sm" variant="secondary" onClick={async () => { try { await providersApi.update(rec.patch!.provider_id, rec.patch!.updates); setRecommendationError('') } catch { setRecommendationError('No se pudo aplicar la recomendación') } }}>Aplicar</Button>}</div>)}
        {recommendationError && <p className="text-xs text-red-600 mt-2">{recommendationError}</p>}
        {save.isError && <p className="text-xs text-red-600 mt-2">{errorDetail(save.error)}</p>}
        {dirty && <Button size="sm" className="mt-4" loading={save.isPending} onClick={saveConfig}>Guardar</Button>}
      </>}
    </Card>
  )
}
