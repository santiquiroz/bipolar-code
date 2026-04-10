import { useState } from 'react'
import { Button } from '@/components/Button'
import { useAddProvider } from '@/hooks/useProviders'

interface Props {
  onClose: () => void
}

const PRESETS = [
  {
    label: 'OpenAI',
    id: 'openai', name: 'OpenAI', api_base: 'https://api.openai.com/v1',
    litellm_prefix: 'openai', auth_env_var: 'OPENAI_API_KEY',
    models_endpoint: 'https://api.openai.com/v1/models', active_model: 'gpt-4o',
  },
  {
    label: 'Azure OpenAI',
    id: 'azure', name: 'Azure OpenAI', api_base: 'https://<resource>.openai.azure.com',
    litellm_prefix: 'azure', auth_env_var: 'AZURE_API_KEY',
    active_model: 'gpt-4o',
  },
  {
    label: 'Groq',
    id: 'groq', name: 'Groq', api_base: 'https://api.groq.com/openai/v1',
    litellm_prefix: 'groq', auth_env_var: 'GROQ_API_KEY',
    models_endpoint: 'https://api.groq.com/openai/v1/models', active_model: 'llama-3.3-70b-versatile',
  },
  {
    label: 'Ollama (local)',
    id: 'ollama', name: 'Ollama', api_base: 'http://localhost:11434/v1',
    litellm_prefix: 'openai', auth_env_var: '',
    models_endpoint: 'http://localhost:11434/v1/models', active_model: 'llama3',
  },
  {
    label: 'Personalizado',
    id: '', name: '', api_base: '', litellm_prefix: 'openai', auth_env_var: '',
  },
]

export function AddProviderModal({ onClose }: Props) {
  const [form, setForm] = useState({
    id: '', name: '', description: '', api_base: '', litellm_prefix: 'openai',
    auth_env_var: '', models_endpoint: '', active_model: '',
    use_chat_completions_for_anthropic: false,
  })
  const add = useAddProvider()

  const applyPreset = (preset: typeof PRESETS[0]) => {
    setForm(f => ({
      ...f,
      id: preset.id,
      name: preset.name,
      api_base: preset.api_base,
      litellm_prefix: preset.litellm_prefix,
      auth_env_var: preset.auth_env_var,
      models_endpoint: preset.models_endpoint ?? '',
      active_model: preset.active_model ?? '',
    }))
  }

  const set = (key: string, value: string | boolean) =>
    setForm(f => ({ ...f, [key]: value }))

  const handleSubmit = () => {
    if (!form.id || !form.name || !form.api_base) return
    add.mutate(
      { ...form, models_endpoint: form.models_endpoint || undefined },
      { onSuccess: onClose }
    )
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="p-6 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-800">Agregar proveedor</h2>
          <p className="text-sm text-gray-500 mt-1">Configura cualquier API compatible con OpenAI o Anthropic</p>
        </div>

        <div className="p-6 space-y-5">
          {/* Presets */}
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Plantilla rápida</p>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map(p => (
                <button
                  key={p.label}
                  onClick={() => applyPreset(p)}
                  className="px-3 py-1.5 text-xs rounded-lg border border-gray-200 hover:border-brand-400 hover:bg-brand-50 transition-colors"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="ID único *" placeholder="openai" value={form.id} onChange={v => set('id', v)} />
            <Field label="Nombre *" placeholder="OpenAI" value={form.name} onChange={v => set('name', v)} />
          </div>
          <Field label="URL base API *" placeholder="https://api.openai.com/v1" value={form.api_base} onChange={v => set('api_base', v)} />
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Prefijo litellm *</label>
              <select
                value={form.litellm_prefix}
                onChange={e => set('litellm_prefix', e.target.value)}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-400"
              >
                {['openai', 'anthropic', 'azure', 'groq', 'ollama', 'together_ai', 'cohere'].map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
            <Field label="Variable .env para auth" placeholder="OPENAI_API_KEY" value={form.auth_env_var} onChange={v => set('auth_env_var', v)} />
          </div>
          <Field label="Endpoint de modelos (opcional)" placeholder="https://api.../models" value={form.models_endpoint} onChange={v => set('models_endpoint', v)} />
          <Field label="Modelo inicial" placeholder="gpt-4o" value={form.active_model} onChange={v => set('active_model', v)} />
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="chat_compat"
              checked={form.use_chat_completions_for_anthropic}
              onChange={e => set('use_chat_completions_for_anthropic', e.target.checked)}
              className="rounded"
            />
            <label htmlFor="chat_compat" className="text-sm text-gray-600">
              Normalizar formato Anthropic → OpenAI Chat Completions
            </label>
          </div>

          {add.isError && (
            <p className="text-sm text-red-600">Error al agregar — verifica que el ID no exista ya.</p>
          )}
        </div>

        <div className="p-6 border-t border-gray-100 flex justify-end gap-3">
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button
            loading={add.isPending}
            disabled={!form.id || !form.name || !form.api_base}
            onClick={handleSubmit}
          >
            Agregar proveedor
          </Button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, placeholder, value, onChange }: {
  label: string; placeholder: string; value: string; onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      <input
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-400"
      />
    </div>
  )
}
