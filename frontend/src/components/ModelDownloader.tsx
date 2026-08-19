import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/Button'
import { Badge } from '@/components/Badge'
import { llamacppApi } from '@/services/api'
import { useHFDownloads, useLocalModels, useStartDownload, useUseModel } from '@/hooks/useLlamaCpp'

function formatSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`
  return `${bytes} B`
}

export function ModelDownloader({ activeModelPath }: { activeModelPath: string }) {
  const [query, setQuery] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null)

  const search = useQuery({
    queryKey: ['llamacpp', 'hf-search', searchTerm],
    queryFn: () => llamacppApi.searchHF(searchTerm),
    enabled: searchTerm.length > 1,
    staleTime: 60_000,
  })
  const files = useQuery({
    queryKey: ['llamacpp', 'hf-files', selectedRepo],
    queryFn: () => llamacppApi.listHFFiles(selectedRepo!),
    enabled: !!selectedRepo,
    staleTime: 60_000,
  })
  const { data: downloadsData } = useHFDownloads()
  const { data: localData } = useLocalModels()
  const startDownload = useStartDownload()
  const useModel = useUseModel()

  const downloads = downloadsData?.downloads ?? []
  const active = downloads.filter(d => d.status === 'downloading' || d.status === 'queued')

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setSearchTerm(query.trim())}
          placeholder="Buscar GGUF en Hugging Face (ej: Qwen3-Coder-Next)"
          className="flex-1 text-xs border border-gray-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-400"
        />
        <Button size="sm" variant="secondary" loading={search.isFetching} onClick={() => setSearchTerm(query.trim())}>
          Buscar
        </Button>
      </div>

      {search.data && !selectedRepo && (
        <div className="max-h-40 overflow-y-auto space-y-1">
          {search.data.results.map(r => (
            <button
              key={r.id}
              onClick={() => setSelectedRepo(r.id)}
              className="w-full text-left text-xs px-2 py-1.5 rounded-lg hover:bg-gray-50 flex items-center gap-2"
            >
              <span className="truncate font-mono">{r.id}</span>
              <span className="ml-auto text-gray-400 shrink-0">{r.downloads.toLocaleString()} ↓</span>
            </button>
          ))}
          {search.data.results.length === 0 && (
            <p className="text-xs text-gray-400">Sin resultados</p>
          )}
        </div>
      )}

      {selectedRepo && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <button onClick={() => setSelectedRepo(null)} className="text-xs text-brand-600 hover:underline">
              ← volver
            </button>
            <span className="text-xs font-mono text-gray-600 truncate">{selectedRepo}</span>
          </div>
          <div className="max-h-40 overflow-y-auto space-y-1">
            {files.data?.files.map(f => (
              <div key={f.filename} className="flex items-center gap-2 text-xs px-2 py-1">
                <span className="truncate font-mono">{f.filename}</span>
                {f.parts.length > 1 && <Badge label={`${f.parts.length} partes`} variant="neutral" />}
                <span className="ml-auto text-gray-400 shrink-0">{formatSize(f.size)}</span>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={startDownload.isPending}
                  onClick={() => startDownload.mutate({ repoId: selectedRepo, filename: f.filename })}
                >
                  Descargar
                </Button>
              </div>
            ))}
            {files.isLoading && <p className="text-xs text-gray-400">Cargando archivos…</p>}
          </div>
        </div>
      )}

      {active.length > 0 && (
        <div className="space-y-2">
          {active.map(d => {
            const pct = d.total_bytes > 0 ? Math.min(100, (d.downloaded_bytes / d.total_bytes) * 100) : 0
            return (
              <div key={d.id} className="space-y-1">
                <div className="flex items-center gap-2 text-xs text-gray-600">
                  <span className="truncate font-mono">{d.filename}</span>
                  <span className="ml-auto text-gray-400 shrink-0">
                    {formatSize(d.downloaded_bytes)} / {formatSize(d.total_bytes)}
                    {d.speed_bps > 0 && ` — ${formatSize(d.speed_bps)}/s`}
                  </span>
                  <button
                    onClick={() => llamacppApi.cancelDownload(d.id)}
                    className="text-red-500 hover:underline shrink-0"
                  >
                    cancelar
                  </button>
                </div>
                <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                  <div className="h-full bg-brand-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {downloads.filter(d => d.status === 'error').map(d => (
        <p key={d.id} className="text-xs text-red-600 truncate">
          Error descargando {d.filename}: {d.error}
        </p>
      ))}

      {(localData?.models.length ?? 0) > 0 && (
        <div className="space-y-1">
          <p className="text-xs text-gray-400">Modelos locales</p>
          {localData!.models.map(m => (
            <div key={m.path} className="flex items-center gap-2 text-xs px-2 py-1">
              <span className="truncate font-mono">{m.filename}</span>
              {activeModelPath === m.path && <Badge label="En uso" variant="success" />}
              <span className="ml-auto text-gray-400 shrink-0">{formatSize(m.size)}</span>
              {activeModelPath !== m.path && (
                <Button size="sm" variant="secondary" loading={useModel.isPending} onClick={() => useModel.mutate(m.path)}>
                  Usar
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
