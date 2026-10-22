import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import type { LlamaDevice } from '@/types/provider'

export type DeviceSelection = 'auto' | string[]

interface LlamaDeviceListProps {
  devices: LlamaDevice[]
  value: DeviceSelection
  onChange: (value: DeviceSelection) => void
}

function formatGiB(mib: number): string {
  return `${(mib / 1024).toFixed(1)} GiB`
}

/** Ids usados en modo automático: todas salvo integradas (o todas si no hay otra). */
function autoIds(devices: LlamaDevice[]): string[] {
  const nonIntegrated = devices.filter((d) => !d.integrated)
  return (nonIntegrated.length > 0 ? nonIntegrated : devices).map((d) => d.id)
}

export function LlamaDeviceList({ devices, value, onChange }: LlamaDeviceListProps) {
  if (devices.length === 0) {
    return <p className="text-xs text-gray-400">Sin GPUs detectadas</p>
  }

  const isAuto = value === 'auto'
  const auto = autoIds(devices)
  const checked = new Set(isAuto ? auto : value)
  // En manual se respeta el orden guardado; las no marcadas van al final.
  const ordered = isAuto
    ? devices
    : [
        ...value.flatMap((id) => devices.filter((d) => d.id === id)),
        ...devices.filter((d) => !value.includes(d.id)),
      ]

  const handleToggle = (id: string, nextChecked: boolean) => {
    const next = new Set(checked)
    if (nextChecked) {
      next.add(id)
    } else {
      next.delete(id)
    }
    onChange(ordered.filter((d) => next.has(d.id)).map((d) => d.id))
  }

  const handleMove = (index: number, delta: -1 | 1) => {
    const next = [...ordered]
    const other = index + delta
    if (other < 0 || other >= next.length) return
    ;[next[index], next[other]] = [next[other], next[index]]
    onChange(next.filter((d) => checked.has(d.id)).map((d) => d.id))
  }

  return (
    <div className="space-y-1">
      <label className="flex items-center gap-2 text-xs text-gray-500">
        <input
          type="checkbox"
          checked={isAuto}
          onChange={(e) => onChange(e.target.checked ? 'auto' : [...auto])}
        />
        Automático (sin integradas)
      </label>
      {ordered.map((d, i) => (
        <div key={d.id} className="flex items-center gap-2 text-xs text-gray-600">
          <input
            type="checkbox"
            aria-label={d.name}
            checked={checked.has(d.id)}
            onChange={(e) => handleToggle(d.id, e.target.checked)}
          />
          <Badge label={d.id} variant="neutral" />
          <span className="truncate">{d.name}</span>
          {d.integrated && <Badge label="integrada" variant="neutral" />}
          <span className="ml-auto text-gray-400 shrink-0">
            {formatGiB(d.vram_free_mib)} libre / {formatGiB(d.vram_total_mib)}
          </span>
          <Button
            size="sm"
            variant="secondary"
            aria-label={`Subir ${d.name}`}
            disabled={i === 0}
            onClick={() => handleMove(i, -1)}
          >
            ↑
          </Button>
          <Button
            size="sm"
            variant="secondary"
            aria-label={`Bajar ${d.name}`}
            disabled={i === ordered.length - 1}
            onClick={() => handleMove(i, 1)}
          >
            ↓
          </Button>
        </div>
      ))}
    </div>
  )
}
