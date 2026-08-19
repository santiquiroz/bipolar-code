import { useState, useEffect, useCallback } from 'react'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'
import { Spinner } from '@/components/Spinner'
import { useEnvVars, useSetEnvKey } from '@/hooks/useSettings'
import { useProviders } from '@/hooks/useProviders'
import { settingsApi } from '@/services/api'

function EnvField({ envKey, label, hint, masked }: {
  envKey: string; label: string; hint: string; masked: string
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
          <code className="text-xs text-gray-400">{envKey}</code>
        </div>
        {!editing && (
          <div className="flex items-center gap-2 shrink-0">
            <code className="text-xs bg-gray-100 text-gray-500 px-2 py-1 rounded font-mono">
              {masked || '— no configurado —'}
            </code>
            <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
              {masked ? 'Cambiar' : 'Configurar'}
            </Button>
          </div>
        )}
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
          <Button size="sm" loading={setKey.isPending} onClick={handleSave}>Guardar</Button>
          <Button variant="secondary" size="sm" onClick={() => { setEditing(false); setValue('') }}>Cancelar</Button>
        </div>
      )}
    </div>
  )
}

export function Settings() {
  const { data: env, isLoading: envLoading } = useEnvVars()
  const { data: registry } = useProviders()
  const [authInfo, setAuthInfo] = useState<{
    api_key_prefix: string
    api_key_length: number
    rate_limit_rpm: number
    allowed_origins: string
  } | null>(null)
  const [fullKey, setFullKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [lanUrl, setLanUrl] = useState<string | null>(null)

  useEffect(() => {
    settingsApi.getAuthInfo().then(setAuthInfo).catch(() => {})
    settingsApi.getConnectionInfo()
      .then(info => setLanUrl(info.anthropic_base_url || null))
      .catch(() => {})
  }, [])

  const loadAndCopyKey = useCallback(async () => {
    try {
      const data = await settingsApi.getApiKey()
      setFullKey(data.api_key)
      await navigator.clipboard.writeText(data.api_key)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {}
  }, [])

  // Recolectar todas las variables de autenticación de los proveedores
  const providerVars = registry?.providers
    .filter(p => p.auth_env_var)
    .map(p => ({
      key: p.auth_env_var,
      label: `Token / API Key`,
      hint: `Variable para ${p.name}`,
      provider: p.name,
    })) ?? []

  // Deduplicar por key
  const uniqueVars = providerVars.filter(
    (v, i, arr) => arr.findIndex(x => x.key === v.key) === i
  )

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">Configuración</h2>
        <p className="text-sm text-gray-500 mt-1">
          Variables guardadas en <code className="text-xs bg-gray-100 px-1 rounded">C:/litellm/.env</code>.
          Los valores se muestran enmascarados.
        </p>
      </div>

      {envLoading ? (
        <Spinner className="h-5 w-5 text-brand-500" />
      ) : (
        <>
          {/* Variables de proveedores configurados */}
          {uniqueVars.length > 0 && (
            <Card title="Autenticación de proveedores">
              {uniqueVars.map(v => (
                <EnvField
                  key={v.key}
                  envKey={v.key}
                  label={`${v.provider} — ${v.label}`}
                  hint={v.key}
                  masked={env?.[v.key] ?? ''}
                />
              ))}
            </Card>
          )}

          {/* Todas las variables del .env */}
          <Card title="Variables de entorno (.env)">
            <p className="text-xs text-gray-400 mb-3">
              Todas las variables actualmente en el archivo. Para agregar una nueva, actívala desde un proveedor.
            </p>
            {env && Object.keys(env).length > 0 ? (
              Object.entries(env).map(([key, masked]) => (
                <EnvField
                  key={key}
                  envKey={key}
                  label={key}
                  hint={key}
                  masked={masked}
                />
              ))
            ) : (
              <p className="text-sm text-gray-400">No se encontraron variables</p>
            )}
          </Card>
        </>
      )}

      <div className="mt-6 bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-700">Acceso y Seguridad</h3>
        <div className="space-y-2 text-sm text-gray-600">
          <div className="flex items-center justify-between">
            <span>API Key</span>
            <code className="bg-gray-100 px-2 py-0.5 rounded text-xs font-mono">
              {authInfo?.api_key_prefix ?? '—'}
            </code>
          </div>
          <div className="flex items-center justify-between">
            <span>Rate limit</span>
            <span className="text-gray-500">{authInfo?.rate_limit_rpm ?? 0} req/min</span>
          </div>
          <div className="flex items-center justify-between">
            <span>Orígenes permitidos</span>
            <span className="text-gray-500 text-xs">{authInfo?.allowed_origins ?? '*'}</span>
          </div>
        </div>

        {/* Sección para red compartida */}
        <div className="border-t border-gray-100 pt-4 space-y-2">
          <p className="text-xs font-medium text-gray-600">Conectar PCs remotas (red compartida)</p>
          <p className="text-xs text-gray-400">
            Configura estas variables en <code className="bg-gray-100 px-1 rounded">~/.claude/settings.json</code> de cada PC:
          </p>
          <div className="bg-gray-50 rounded-lg p-3 space-y-1 font-mono text-xs text-gray-600">
            <div><span className="text-gray-400">ANTHROPIC_BASE_URL</span> = {lanUrl ?? 'http://<ip-servidor>:8000'}</div>
            <div className="flex items-center gap-2">
              <span className="text-gray-400">ANTHROPIC_API_KEY</span> ={' '}
              <span className="text-gray-500">{fullKey ?? authInfo?.api_key_prefix ?? '…'}</span>
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={loadAndCopyKey}>
            {copied ? '¡Copiado!' : 'Copiar API Key completa'}
          </Button>
        </div>
      </div>
    </div>
  )
}
