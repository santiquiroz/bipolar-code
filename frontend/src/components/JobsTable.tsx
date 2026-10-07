import { Button } from '@/components/Button'
import { Badge } from '@/components/Badge'
import type { GateStatus, Job } from '@/types/smart'

const gateVariant = (s: GateStatus) => s === 'passed' ? 'success' as const : s === 'failed' ? 'error' as const : s === 'skipped' ? 'warning' as const : 'neutral' as const
const statusVariant = (s: string) => ['succeeded', 'available', 'ok'].includes(s) ? 'success' as const : ['failed', 'error', 'auth_error'].includes(s) ? 'error' as const : 'warning' as const

interface Props {
  jobs: Job[]
  onLog: (job: Job) => void
  onCancel: (id: string) => void
}

export function JobsTable({ jobs, onLog, onCancel }: Props) {
  return <table className="w-full text-xs mt-4"><thead><tr className="text-gray-400 text-left border-b"><th>Estado</th><th>Tier</th><th>Agente</th><th>Intentos</th><th>Archivos</th><th>Verificación</th><th>Revisión</th><th>Escalados</th><th /></tr></thead><tbody>{jobs.map(job => <tr key={job.id} className="border-b border-gray-50"><td className="py-2"><Badge label={job.status} variant={statusVariant(job.status)} /></td><td>{job.tier}</td><td>{job.agent_id || '—'}</td><td>{job.attempts.length}</td><td>{job.files_touched.length}</td><td><Badge label={job.verification_status} variant={gateVariant(job.verification_status)} /></td><td><Badge label={job.review_status} variant={gateVariant(job.review_status)} /></td><td>{job.escalations}</td><td><Button size="sm" variant="secondary" onClick={() => onLog(job)}>Log</Button>{['queued', 'running'].includes(job.status) && <Button size="sm" variant="danger" onClick={() => onCancel(job.id)}>Cancelar</Button>}</td></tr>)}</tbody></table>
}
