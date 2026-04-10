import { useState } from 'react'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'
import { Spinner } from '@/components/Spinner'
import { useEnvVars, useSetEnvKey } from '@/hooks/useSettings'

const ENV_GROUPS: { label: string; keys: { key: string; label: string; hint: string }[] }[] = [
  {
    label: 'Anthropic',
    keys: [
      { key: 'ANTHROPIC_API_KEY', label: 'API Key', hint: 'sk-ant-api03-...' },
    ],
  },
  {
    label: 'GitHub Copilot',
    keys: [
      { key: 'COPILOT_SESSION_TOKEN', label: 'Session Token', hint: 'tid=... (se renueva cada 25 min)' },
      { key: 'GITHUB_OAUTH_TOKEN', label: 'OAuth Token', hint: 'gho_... (para renovar el session token)' },
    ],
  },
  {
    label: 'GitHub Models',
    keys: [
      { key: 'GITHUB_TOKEN', label: 'GitHub Token', hint: 'ghp_...' },
    ],
  },
]

function EnvField({ envKey, label, hint, masked }: {
  envKey: string
  label: string
  hint: string
  masked: string
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const setKey = useSetEnvKey()

  const handleSave = () => {
    if (!value.trim()) return
    setKey.mutate({ key: envKey, value: value.trim() }, {
      onSuccess: () => { setEditing(false); setValue('') },
    })
  }

  return (
    <div className="py-3 border-b border-gray-50 last:border-0">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-700">{label}</p>
          <p className="text-xs text-gray-400 font-mono mt-0.5">{envKey}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!editing && (
            <>
              <code className="text-xs bg-gray-100 text-gray-500 px-2 py-1 rounded font-mono">
                {masked || '— no configurado —'}
              </code>
              <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
                {masked ? 'Cambiar' : 'Configurar'}
              </Button>
            </>
          )}
        </div>
      </div>
      {editing && (
        <div className="mt-2 flex gap-2">
          <input
            type="password"
            autoFocus
            placeholder={hint}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSave()}
            className="flex-1 text-sm border border-gray-300 rounded-lg px-3 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-400"
          />
          <Button size="sm" loading={setKey.isPending} onClick={handleSave}>
            Guardar
          </Button>
          <Button variant="secondary" size="sm" onClick={() => { setEditing(false); setValue('') }}>
            Cancelar
          </Button>
        </div>
      )}
      {setKey.isError && editing === false && (
        <p className="text-xs text-red-500 mt-1">Error al guardar</p>
      )}
    </div>
  )
}

export function Settings() {
  const { data: env, isLoading } = useEnvVars()

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">Configuración</h2>
        <p className="text-sm text-gray-500 mt-1">
          Los valores se guardan en <code className="text-xs bg-gray-100 px-1 rounded">C:/litellm/.env</code>.
          Los tokens enmascarados nunca se transmiten completos.
        </p>
      </div>

      {isLoading ? (
        <Spinner className="h-5 w-5 text-brand-500" />
      ) : (
        ENV_GROUPS.map(group => (
          <Card key={group.label} title={group.label}>
            {group.keys.map(({ key, label, hint }) => (
              <EnvField
                key={key}
                envKey={key}
                label={label}
                hint={hint}
                masked={env?.[key] ?? ''}
              />
            ))}
          </Card>
        ))
      )}
    </div>
  )
}
