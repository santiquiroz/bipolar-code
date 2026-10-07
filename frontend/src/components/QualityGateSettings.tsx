import type { DelegationConfig } from '@/types/smart'

const field = 'border border-gray-300 rounded-lg px-2 py-1.5 text-xs'

interface Props {
  delegation: DelegationConfig
  onChange: (patch: Partial<DelegationConfig>) => void
}

export function QualityGateSettings({ delegation, onChange }: Props) {
  return <div className="mt-3 space-y-2 border-t border-gray-100 pt-3">
    <label className="block text-xs text-gray-500">Pensadores (revisan y reciben escalados)<input className={`${field} w-full mt-1`} value={delegation.thinkers.join(', ')} onChange={e => onChange({ thinkers: e.target.value.split(',').map(x => x.trim()).filter(Boolean) })} /></label>
    <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={delegation.review_default} onChange={e => onChange({ review_default: e.target.checked })} /> Revisar siempre con un pensador</label>
    <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" checked={delegation.allow_request_verify} onChange={e => onChange({ allow_request_verify: e.target.checked })} /> <span>Permitir comandos de verificación en el request <span className="text-amber-600">(corren en este PC sin sandbox)</span></span></label>
    <div className="grid grid-cols-2 gap-3"><label className="text-xs text-gray-500">Timeout de verificación (s)<input className={`${field} w-full mt-1`} type="number" value={delegation.verify_timeout_s} onChange={e => onChange({ verify_timeout_s: Number(e.target.value) })} /></label><label className="text-xs text-gray-500">Timeout de revisión (s)<input className={`${field} w-full mt-1`} type="number" value={delegation.review_timeout_s} onChange={e => onChange({ review_timeout_s: Number(e.target.value) })} /></label></div>
  </div>
}
