import { Badge } from '@/components/Badge'
import type { Attempt } from '@/types/smart'

const kindLabel = (kind: Attempt['kind']) => kind === 'verify' ? 'verificación' : kind === 'review' ? 'revisión' : 'trabajo'

function VerifyDetail({ detail }: { detail: Attempt['detail'] }) {
  return <span className="text-gray-600"><code className="bg-gray-50 rounded px-1">{String(detail.command ?? '')}</code> · salida: {String(detail.returncode ?? '—')}</span>
}

function ReviewDetail({ detail }: { detail: Attempt['detail'] }) {
  const issues = Array.isArray(detail.issues) ? detail.issues.map(String) : []
  return <span className="text-gray-600">veredicto: {String(detail.verdict || '—')}{issues.length > 0 ? ` · ${issues.join('; ')}` : ''}</span>
}

function AttemptDetail({ attempt }: { attempt: Attempt }) {
  if (attempt.kind === 'verify') return <VerifyDetail detail={attempt.detail} />
  if (attempt.kind === 'review') return <ReviewDetail detail={attempt.detail} />
  return <span className="text-gray-600">{attempt.model}{attempt.detail?.revision ? ' (corrección)' : ''}</span>
}

export function AttemptList({ attempts }: { attempts: Attempt[] }) {
  if (attempts.length === 0) return null
  return <ul className="mt-4 space-y-1 text-xs">{attempts.map((a, i) => <li key={i} className="flex flex-wrap items-center gap-2"><Badge label={kindLabel(a.kind)} variant="neutral" /><span className="font-medium text-gray-800">{a.agent_id}</span><AttemptDetail attempt={a} /></li>)}</ul>
}
