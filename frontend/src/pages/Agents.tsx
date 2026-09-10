import { useEffect, useState } from 'react'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'
import { Badge } from '@/components/Badge'
import { delegateApi } from '@/services/api'
import { useAgents, useClassify, useProbeAgent, useResetHealth, useSaveSmartConfig, useSmartConfig } from '@/hooks/useSmart'
import { useCancelJob, useJobs, useSubmitJob } from '@/hooks/useDelegate'
import type { CliAgent, DelegationConfig, Job, Tier } from '@/types/smart'

const tiers: Tier[] = ['trivial', 'simple', 'standard', 'complex']
const field = 'border border-gray-300 rounded-lg px-2 py-1.5 text-xs'
const detail = (e: unknown) => (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Error al enviar'

function statusVariant(status: string) { return ['succeeded', 'available', 'ok'].includes(status) ? 'success' as const : ['failed', 'error', 'auth_error'].includes(status) ? 'error' as const : 'warning' as const }

export function Agents() {
  const { data: config, isLoading } = useSmartConfig()
  const { data: agentData } = useAgents()
  const save = useSaveSmartConfig()
  const probe = useProbeAgent()
  const reset = useResetHealth()
  const classify = useClassify()
  const jobs = useJobs()
  const submit = useSubmitJob()
  const cancel = useCancelJob()
  const [delegation, setDelegation] = useState<DelegationConfig | null>(null)
  const [agents, setAgents] = useState<CliAgent[]>([])
  const [dirty, setDirty] = useState(false)
  const [task, setTask] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [mode, setMode] = useState<'task' | 'text'>('task')
  const [agentId, setAgentId] = useState('')
  const [dryRun, setDryRun] = useState(false)
  const [classifierText, setClassifierText] = useState('')
  const [drawer, setDrawer] = useState<Job | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [jobError, setJobError] = useState('')
  useEffect(() => {
    if (config && !dirty) {
      if (JSON.stringify(config.delegation) !== JSON.stringify(delegation)) setDelegation(config.delegation)
      if (JSON.stringify(config.cli_agents) !== JSON.stringify(agents)) setAgents(config.cli_agents)
    }
  }, [config, dirty, delegation, agents])
  const updateDelegation = (patch: Partial<DelegationConfig>) => { setDelegation(d => d ? { ...d, ...patch } : d); setDirty(true) }
  const updateAgent = (id: string, patch: Partial<CliAgent>) => { setAgents(list => list.map(a => a.id === id ? { ...a, ...patch } : a)); setDirty(true) }
  const saveAll = () => delegation && save.mutate({ cli_agents: agents, delegation }, { onSuccess: () => setDirty(false) })
  const submitJob = () => { setJobError(''); submit.mutate({ task, workspace, mode, agent_id: agentId, dry_run: dryRun }, { onSuccess: () => setTask(''), onError: e => setJobError(detail(e)) }) }
  const openLog = async (job: Job) => {
    setDrawer(job); setLog([])
    try { await delegateApi.streamJob(job.id, event => { if (event.event === 'line') setLog(old => [...old, String(event.text || '')]); if (event.event === 'attempt') setLog(old => [...old, `Failover: ${event.agent_id} ${event.signal || ''} ${event.excerpt || ''}`]) }) } catch (e) { setLog(old => [...old, String(e)]) }
  }
  if (isLoading || !delegation) return <p className="text-sm text-gray-400">Cargando agentes...</p>
  return <div className="space-y-4">
    <div className="flex justify-between items-center"><div><h2 className="text-lg font-semibold text-gray-800">Agentes</h2><p className="text-sm text-gray-500">Delegación y diagnóstico de agentes CLI</p></div>{dirty && <Button loading={save.isPending} onClick={saveAll}>Guardar</Button>}</div>
    <Card title="Delegación">
      <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={delegation.enabled} onChange={e => updateDelegation({ enabled: e.target.checked })} /> Delegación a agentes CLI</label>
      <label className="block text-xs text-gray-500 mt-3">Workspaces permitidos (una ruta absoluta por línea)<textarea className={`${field} w-full mt-1`} rows={3} value={delegation.workspace_allowlist.join('\n')} onChange={e => updateDelegation({ workspace_allowlist: e.target.value.split(/\r?\n/).filter(Boolean) })} /></label>
      <p className="text-xs text-amber-600 mt-1">Una lista vacía deshabilita la delegación.</p>
      <div className="grid grid-cols-2 gap-3 mt-3"><label className="text-xs text-gray-500">Máximo paralelo<input className={`${field} w-full mt-1`} type="number" value={delegation.max_parallel_jobs} onChange={e => updateDelegation({ max_parallel_jobs: Number(e.target.value) })} /></label><label className="text-xs text-gray-500">Máximos intentos<input className={`${field} w-full mt-1`} type="number" value={delegation.max_attempts} onChange={e => updateDelegation({ max_attempts: Number(e.target.value) })} /></label></div>
      <div className="space-y-2 mt-3">{tiers.map(tier => <label key={tier} className="flex items-center gap-2 text-xs text-gray-500"><span className="w-16">{tier}</span><input className={`${field} flex-1`} value={(delegation.tier_order[tier] || []).join(', ')} onChange={e => updateDelegation({ tier_order: { ...delegation.tier_order, [tier]: e.target.value.split(',').map(x => x.trim()).filter(Boolean) } })} /></label>)}</div>
    </Card>
    <Card title="Agentes CLI">
      <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="text-gray-400 text-left border-b"><th className="pb-2">Agente</th><th>Estado</th><th>Modelo</th><th>Timeout</th><th>Créditos</th><th>Acciones</th></tr></thead><tbody>{agents.map(agent => { const st = agentData?.agents.find(a => a.id === agent.id); return <tr key={agent.id} className="border-b border-gray-50 align-top"><td className="py-2"><div className="font-medium">{agent.name}</div><div className="mt-1">{st?.installed ? <Badge label={`Instalado ${st.version}`} variant="success" /> : <Badge label="No encontrado" variant="warning" />} <Badge label={st?.auth || 'unknown'} variant={statusVariant(st?.auth || 'unknown')} /></div><label className="block mt-1"><input type="checkbox" checked={agent.enabled} onChange={e => updateAgent(agent.id, { enabled: e.target.checked })} /> habilitado</label></td><td className="py-2"><Badge label={st?.state || 'unknown'} variant={statusVariant(st?.state || 'unknown')} />{st?.seconds_left ? <div>{Math.ceil(st.seconds_left)}s</div> : null}{agent.id === 'antigravity' && st?.quota && Object.entries(st.quota).map(([k, q]) => <div key={k}>{k}: {Math.round((q.remaining_fraction || 0) * 100)}%</div>)}</td><td className="py-2"><input className={field} value={agent.default_model} onChange={e => updateAgent(agent.id, { default_model: e.target.value })} /></td><td className="py-2"><input className={`${field} w-20`} type="number" value={agent.timeout_s} onChange={e => updateAgent(agent.id, { timeout_s: Number(e.target.value) })} /></td><td className="py-2">{agent.id === 'copilot' && <input className={`${field} w-20`} type="number" value={agent.max_credits} onChange={e => updateAgent(agent.id, { max_credits: Number(e.target.value) })} />}</td><td className="py-2 space-x-1"><select className={field} value={agent.quota_reset} onChange={e => updateAgent(agent.id, { quota_reset: e.target.value as CliAgent['quota_reset'] })}><option>none</option><option>5h</option><option>daily</option><option>weekly</option></select><Button size="sm" variant="secondary" loading={probe.isPending && probe.variables === agent.id} onClick={() => probe.mutate(agent.id)}>Probar</Button><Button size="sm" variant="secondary" onClick={() => reset.mutate(`cli:${agent.id}`)}>Reiniciar estado</Button></td></tr> })}</tbody></table></div>
    </Card>
    <Card title="Probador de clasificador"><textarea className={`${field} w-full`} rows={3} value={classifierText} onChange={e => setClassifierText(e.target.value)} placeholder="Describe la tarea..." /><Button size="sm" className="mt-2" loading={classify.isPending} onClick={() => classify.mutate({ task: classifierText })}>Clasificar</Button>{classify.data && <div className="mt-2 text-xs"><Badge label={`${classify.data.tier} · ${classify.data.score}`} variant="info" /> {classify.data.intent}<ul className="list-disc ml-5">{classify.data.reasons.map(r => <li key={r}>{r}</li>)}</ul></div>}</Card>
    <Card title="Trabajos"><div className="space-y-2"><textarea className={`${field} w-full`} rows={3} value={task} onChange={e => setTask(e.target.value)} placeholder="Tarea a delegar" /><div className="flex flex-wrap gap-2"><select className={field} value={workspace} onChange={e => setWorkspace(e.target.value)}><option value="">Workspace</option>{delegation.workspace_allowlist.map(w => <option key={w}>{w}</option>)}</select><select className={field} value={mode} onChange={e => setMode(e.target.value as 'task' | 'text')}><option value="task">task</option><option value="text">text</option></select><select className={field} value={agentId} onChange={e => setAgentId(e.target.value)}><option value="">Automático</option>{agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select><label className="text-xs"><input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} /> dry-run</label><Button size="sm" onClick={submitJob} loading={submit.isPending}>Enviar</Button></div>{jobError && <p className="text-xs text-red-600">{jobError}</p>}</div><table className="w-full text-xs mt-4"><thead><tr className="text-gray-400 text-left border-b"><th>Estado</th><th>Tier</th><th>Agente</th><th>Intentos</th><th>Archivos</th><th /></tr></thead><tbody>{(jobs.data?.jobs || []).map(job => <tr key={job.id} className="border-b border-gray-50"><td className="py-2"><Badge label={job.status} variant={statusVariant(job.status)} /></td><td>{job.tier}</td><td>{job.agent_id || '—'}</td><td>{job.attempts.length}</td><td>{job.files_touched.length}</td><td><Button size="sm" variant="secondary" onClick={() => openLog(job)}>Log</Button>{['queued', 'running'].includes(job.status) && <Button size="sm" variant="danger" onClick={() => cancel.mutate(job.id)}>Cancelar</Button>}</td></tr>)}</tbody></table></Card>
    {drawer && <div className="fixed inset-y-0 right-0 w-full max-w-lg bg-white shadow-xl border-l p-6 z-10"><div className="flex justify-between"><h3 className="font-semibold">Log {drawer.id}</h3><button onClick={() => setDrawer(null)}>✕</button></div><pre className="text-xs whitespace-pre-wrap mt-4 max-h-[80vh] overflow-auto">{log.join('\n') || drawer.output_tail}</pre></div>}
  </div>
}
