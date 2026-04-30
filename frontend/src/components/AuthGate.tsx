import { useState, useEffect } from 'react'
import { getStoredApiKey, setStoredApiKey, validateApiKey } from '@/services/api'

interface AuthGateProps {
  children: React.ReactNode
}

export function AuthGate({ children }: AuthGateProps) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [inputKey, setInputKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showKey, setShowKey] = useState(false)

  useEffect(() => {
    const stored = getStoredApiKey()
    if (!stored) {
      setAuthenticated(false)
      return
    }
    validateApiKey(stored).then(valid => {
      setAuthenticated(valid)
    })
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputKey.trim()) return
    setLoading(true)
    setError('')
    const valid = await validateApiKey(inputKey.trim())
    if (valid) {
      setStoredApiKey(inputKey.trim())
      setAuthenticated(true)
    } else {
      setError('API key inválida. Revisa C:\\litellm\\.env (variable UI_API_KEY).')
    }
    setLoading(false)
  }

  if (authenticated === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!authenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-full max-w-sm p-8 bg-white rounded-2xl shadow-lg border border-gray-100 space-y-6">
          <div className="text-center space-y-1">
            <h1 className="text-xl font-bold text-gray-900">Bipolar Code</h1>
            <p className="text-sm text-gray-500">Ingresa la API key para continuar</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                className="w-full pr-10 pl-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-brand-400 focus:border-brand-400 outline-none font-mono"
                placeholder="bc-xxxxxxxxxxxxxxxx..."
                value={inputKey}
                onChange={e => setInputKey(e.target.value)}
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShowKey(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                aria-label={showKey ? 'Ocultar key' : 'Mostrar key'}
              >
                {showKey ? (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 4.411m0 0L21 21" /></svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
                )}
              </button>
            </div>

            {error && <p className="text-xs text-red-500">{error}</p>}

            <button
              type="submit"
              disabled={loading || !inputKey.trim()}
              className="w-full py-2.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
            >
              {loading ? 'Verificando...' : 'Acceder'}
            </button>
          </form>

          <p className="text-xs text-gray-400 text-center">
            La API key se encuentra en{' '}
            <code className="bg-gray-100 px-1 rounded">C:\litellm\.env</code>{' '}
            como <code className="bg-gray-100 px-1 rounded">UI_API_KEY</code>
          </p>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
