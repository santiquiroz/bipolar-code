import { useState } from 'react'
import { verifyKeyApi } from '@/services/api'

interface Props {
  onComplete: () => void
  onClose: () => void
}

export function NvidiaWizard({ onComplete, onClose }: Props) {
  const [step, setStep] = useState(1)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState('')
  const [modelCount, setModelCount] = useState(0)

  const handleVerify = async () => {
    setVerifying(true)
    setError('')
    try {
      const result = await verifyKeyApi.verify('nvidia_nim', apiKey)
      if (result.valid) {
        setModelCount(result.model_count || 0)
        setStep(3)
      } else {
        setError(result.error || 'API key inválida')
      }
    } catch {
      setError('Error de conexión. Verifica tu key e intenta de nuevo.')
    } finally {
      setVerifying(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl p-6 w-full max-w-md border border-gray-200 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-gray-800 font-semibold text-lg">Configurar NVIDIA NIM</h2>
            <p className="text-gray-400 text-sm">Paso {step} de 3</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        {/* Progress */}
        <div className="flex gap-2 mb-6">
          {[1, 2, 3].map(s => (
            <div key={s} className={`flex-1 h-1 rounded-full ${s <= step ? 'bg-emerald-500' : 'bg-gray-200'}`} />
          ))}
        </div>

        {step === 1 && (
          <div className="space-y-4">
            <p className="text-gray-600 text-sm">
              NVIDIA NIM ofrece acceso a modelos Llama, Mistral y más.
              Las cuentas nuevas reciben{' '}
              <strong className="text-emerald-600">$200 USD en créditos gratuitos</strong>.
            </p>
            <ol className="text-sm text-gray-500 space-y-2 list-decimal list-inside">
              <li>Ve a <strong className="text-gray-700">build.nvidia.com</strong> y crea una cuenta</li>
              <li>Verifica tu email</li>
              <li>En el dashboard, abre <strong className="text-gray-700">API Keys</strong></li>
              <li>Clic en <strong className="text-gray-700">+ Generate API Key</strong> y cópiala</li>
            </ol>
            <a
              href="https://build.nvidia.com"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-brand-600 hover:text-brand-700 text-sm"
            >
              Abrir build.nvidia.com →
            </a>
            <button
              onClick={() => setStep(2)}
              className="w-full bg-brand-600 hover:bg-brand-700 text-white py-2 rounded-lg font-medium text-sm"
            >
              Ya tengo mi API Key →
            </button>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4">
            <p className="text-gray-600 text-sm">
              Ingresa tu API Key (comienza con <code className="text-emerald-600 bg-gray-100 px-1 rounded">nvapi-</code>):
            </p>
            <div className="relative">
              <input
                autoFocus
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && apiKey.trim() && handleVerify()}
                placeholder="nvapi-xxxxxxxxxxxxxxxxxxxx"
                className="w-full px-3 py-2 rounded-lg border border-gray-200 focus:border-brand-500 focus:ring-1 focus:ring-brand-500 focus:outline-none pr-10 text-sm"
              />
              <button
                onClick={() => setShowKey(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-sm"
              >
                {showKey ? '🙈' : '👁'}
              </button>
            </div>
            {error && <p className="text-red-500 text-sm">{error}</p>}
            <div className="flex gap-2">
              <button
                onClick={() => setStep(1)}
                className="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-700 py-2 rounded-lg text-sm"
              >
                ← Atrás
              </button>
              <button
                onClick={handleVerify}
                disabled={!apiKey.trim() || verifying}
                className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white py-2 rounded-lg font-medium text-sm"
              >
                {verifying ? 'Verificando...' : 'Verificar y guardar →'}
              </button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="space-y-4 text-center">
            <div className="text-emerald-500 text-5xl">✓</div>
            <p className="text-gray-800 font-medium">¡Conexión exitosa!</p>
            <p className="text-gray-500 text-sm">
              NVIDIA NIM está listo.{' '}
              {modelCount > 0 && <>Modelos disponibles: <strong className="text-gray-700">{modelCount}</strong></>}
            </p>
            <p className="text-gray-400 text-xs">
              Créditos iniciales para cuentas nuevas:{' '}
              <strong className="text-emerald-600">$200 USD</strong>
            </p>
            <button
              onClick={onComplete}
              className="w-full bg-brand-600 hover:bg-brand-700 text-white py-2 rounded-lg font-medium text-sm"
            >
              Activar NVIDIA NIM →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
