import { useState } from 'react'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { useAddPoolKey, useProviderCredentials, useRemovePoolKey } from '@/hooks/useCredentials'
import type { CredentialSlotStatus, Provider } from '@/types/provider'

function stateVariant(state: string): 'success' | 'warning' | 'neutral' {
  if (state === 'available') return 'success'
  if (state === 'exhausted' || state === 'cooling') return 'warning'
  return 'neutral'
}

function formatCooldown(secondsLeft: number): string {
  if (secondsLeft < 60) return `vuelve en ${secondsLeft}s`
  const h = Math.floor(secondsLeft / 3600)
  const m = Math.floor((secondsLeft % 3600) / 60)
  if (h > 0) return `vuelve en ${h}h ${m}m`
  return `vuelve en ${m}m`
}

function SlotRow({ slot, onRemove, removing }: { slot: CredentialSlotStatus; onRemove: (name: string) => void; removing: boolean }) {
  return (
    <div className="flex items-center gap-2 flex-wrap py-1">
      <span className="text-sm text-gray-700 font-mono">{slot.env_var}</span>
      <span className="text-xs text-gray-500">{slot.has_value ? 'con valor' : 'vacía'}</span>
      <Badge label={slot.state} variant={stateVariant(slot.state)} />
      {slot.seconds_left > 0 && (
        <span className="text-xs text-gray-500">{formatCooldown(slot.seconds_left)}</span>
      )}
      {slot.slot > 0 && (
        <Button variant="secondary" size="sm" loading={removing} onClick={() => onRemove(slot.env_var)}>
          Quitar del pool
        </Button>
      )}
    </div>
  )
}

export function CredentialPoolPanel({ provider }: { provider: Provider }) {
  const { data } = useProviderCredentials(provider.id)
  const addKey = useAddPoolKey(provider)
  const removeKey = useRemovePoolKey(provider)
  const [value, setValue] = useState('')
  const inputId = `pool-key-${provider.id}`

  const handleAdd = () => {
    addKey.mutate({ value }, { onSuccess: () => setValue('') })
  }

  return (
    <div className="mt-3 pt-3 border-t border-gray-100">
      <h4 className="text-sm font-semibold text-gray-700">Pool de llaves</h4>
      <div className="mt-1 divide-y divide-gray-50">
        {(data?.slots ?? []).map((slot) => (
          <SlotRow
            key={slot.env_var}
            slot={slot}
            removing={removeKey.isPending}
            onRemove={(name) => removeKey.mutate(name)}
          />
        ))}
        {(data?.missing ?? []).map((name) => (
          <div key={name} className="flex items-center gap-2 flex-wrap py-1">
            <span className="text-sm text-gray-700 font-mono">{name}</span>
            <span className="text-xs text-amber-700">sin valor en .env</span>
            <Button variant="secondary" size="sm" loading={removeKey.isPending} onClick={() => removeKey.mutate(name)}>
              Quitar del pool
            </Button>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-500 mt-2">
        Si una llave se queda sin cuota o es rechazada, el mismo request sigue con la siguiente.
      </p>
      <div className="mt-2 flex items-end gap-2">
        <div className="flex-1">
          <label htmlFor={inputId} className="block text-xs font-medium text-gray-600 mb-1">
            Nueva llave
          </label>
          <input
            id={inputId}
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="w-full rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>
        <Button size="sm" disabled={value.trim() === ''} loading={addKey.isPending} onClick={handleAdd}>
          Agregar
        </Button>
      </div>
    </div>
  )
}
