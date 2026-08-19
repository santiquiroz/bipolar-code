import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/Badge'
import { Button } from '@/components/Button'
import { providersApi } from '@/services/api'
import { useLlamaDevices, useLlamaStatus, useStartLlama, useStopLlama } from '@/hooks/useLlamaCpp'
import type { Provider, LocalLaunchConfig } from '@/types/provider'

interface LlamaCppPanelProps {
  provider: Provider
}

function formatGiB(mib: number): string {
  return `${(mib / 1024).toFixed(1)} GiB`
}

export function LlamaCppPanel({ provider }: LlamaCppPanelProps) {
  const qc = useQueryClient()
  const { data: devicesResp } = useLlamaDevices()
  const { data: status } = useLlamaStatus()
  const startLlama = useStartLlama()
  const stopLlama = useStopLlama()
  const [confirmForce, setConfirmForce] = useState(false)

  const launch: LocalLaunchConfig = provider.local_launch ?? {}
  const [modelPath, setModelPath] = useState(launch.model_path ?? '')
  const [exePath, setExePath] = useState(launch.exe_path ?? '')
  const [ctxSize, setCtxSize] = useState(launch.ctx_size ?? 32768)

  const saveLaunch = useMutation({
    mutationFn: (updates: LocalLaunchConfig) =>
      providersApi.update(provider.id, { local_launch: { ...launch, ...updates } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['providers'] }),
  })

  const handleStop = () => {
    stopLlama.mutate(confirmForce, {
      onError: (err: unknown) => {
        const isBusy = (err as { response?: { status?: number } })?.response?.status === 409
        if (isBusy) setConfirmForce(true)
      },
      onSuccess: () => setConfirmForce(false),
    })
  }

  const devices = devicesResp?.devices ?? []
  const totalFree = devices.reduce((acc, d) => acc + d.vram_free_mib, 0)

  return (
    <div className="mt-3 border-t border-gray-100 pt-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-medium text-gray-700">Servidor local</span>
        {status?.running ? (
          <Badge label={status.healthy ? 'Corriendo' : 'Arrancando…'} variant={status.healthy ? 'success' : 'warning'} />
        ) : (
          <Badge label="Detenido" variant="neutral" />
        )}
        {status?.running && status.busy_slots > 0 && (
          <Badge label={`${status.busy_slots} slot(s) en uso`} variant="warning" />
        )}
        <div className="ml-auto flex gap-2">
          {!status?.running ? (
            <Button size="sm" loading={startLlama.isPending} onClick={() => startLlama.mutate()}>
              Iniciar
            </Button>
          ) : (
            <Button variant="danger" size="sm" loading={stopLlama.isPending} onClick={handleStop}>
              {confirmForce ? 'Forzar detención' : 'Detener'}
            </Button>
          )}
        </div>
      </div>

      {confirmForce && (
        <p className="text-xs text-amber-600">
          Hay requests en curso. Pulsa de nuevo para detener de todos modos.
        </p>
      )}

      {startLlama.isError && (
        <p className="text-xs text-red-600">
          {(startLlama.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Error al iniciar'}
        </p>
      )}

      {devicesResp && !devicesResp.exe_found && (
        <p className="text-xs text-amber-600">
          llama-server no encontrado. Descarga un release Vulkan de llama.cpp
          (github.com/ggml-org/llama.cpp/releases) y configura la ruta abajo.
        </p>
      )}

      {devices.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs text-gray-400">
            GPUs detectadas — VRAM libre total: {formatGiB(totalFree)} (tensor-split auto proporcional)
          </p>
          {devices.map((d) => (
            <div key={d.index} className="flex items-center gap-2 text-xs text-gray-600">
              <Badge label={`${d.backend}${d.index}`} variant="neutral" />
              <span className="truncate">{d.name}</span>
              <span className="ml-auto text-gray-400">
                {formatGiB(d.vram_free_mib)} libre / {formatGiB(d.vram_total_mib)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-2">
        <label className="text-xs text-gray-500">
          Modelo GGUF
          <input
            value={modelPath}
            onChange={(e) => setModelPath(e.target.value)}
            onBlur={() => saveLaunch.mutate({ model_path: modelPath })}
            placeholder="C:\\modelos\\Qwen3-Coder-Next-Q4_K_M.gguf"
            className="mt-1 w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-400"
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-gray-500">
            llama-server (opcional si está en PATH)
            <input
              value={exePath}
              onChange={(e) => setExePath(e.target.value)}
              onBlur={() => saveLaunch.mutate({ exe_path: exePath })}
              placeholder="C:\\llama.cpp\\llama-server.exe"
              className="mt-1 w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-400"
            />
          </label>
          <label className="text-xs text-gray-500">
            Contexto (tokens)
            <input
              type="number"
              value={ctxSize}
              onChange={(e) => setCtxSize(Number(e.target.value))}
              onBlur={() => saveLaunch.mutate({ ctx_size: ctxSize })}
              className="mt-1 w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-brand-400"
            />
          </label>
        </div>
      </div>
    </div>
  )
}
