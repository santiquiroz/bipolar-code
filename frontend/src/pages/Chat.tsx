import { useState, useRef, useEffect, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { modelsApi, providersApi } from '@/services/api'
import type { ReactNode } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

type TextPart = { type: 'text'; text: string }
type ImagePart = { type: 'image_url'; image_url: { url: string } }
type ContentPart = TextPart | ImagePart

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string | ContentPart[]
}

// ── Streaming ─────────────────────────────────────────────────────────────────

async function* streamChat(
  messages: Message[],
  model: string,
  signal: AbortSignal,
): AsyncGenerator<string> {
  const resp = await fetch('/api/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      messages: messages.map(m => ({ role: m.role, content: m.content })),
    }),
    signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)

  const reader = resp.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const data = line.slice(6).trim()
        if (data === '[DONE]') return
        try {
          const chunk = JSON.parse(data)
          if (chunk.error) throw new Error(chunk.error.message ?? 'Error en el stream')
          const text = chunk.choices?.[0]?.delta?.content
          if (text) yield text
        } catch (e) {
          if (e instanceof SyntaxError) continue
          throw e
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

// ── Content renderer ──────────────────────────────────────────────────────────

function renderContent(text: string): ReactNode {
  // Split on fenced code blocks (``` ... ```)
  const parts = text.split(/(```[^\n]*\n[\s\S]*?```)/g)
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('```')) {
          const nl = part.indexOf('\n')
          const lang = part.slice(3, nl).trim()
          const code = part.slice(nl + 1).replace(/```$/, '').trimEnd()
          return (
            <pre
              key={i}
              className="my-3 rounded-xl bg-gray-950 text-gray-100 text-xs font-mono overflow-x-auto"
            >
              {lang && (
                <div className="px-4 pt-2.5 pb-1.5 text-gray-500 text-xs border-b border-gray-800">
                  {lang}
                </div>
              )}
              <code className="block px-4 py-3 whitespace-pre">{code}</code>
            </pre>
          )
        }
        // Plain text: preserve line breaks
        return (
          <span key={i}>
            {part.split('\n').map((line, j, arr) => (
              <span key={j}>
                {line}
                {j < arr.length - 1 && <br />}
              </span>
            ))}
          </span>
        )
      })}
    </>
  )
}

// ── MessageBubble ─────────────────────────────────────────────────────────────

function MessageBubble({
  msg,
  isStreaming,
}: {
  msg: Message
  isStreaming: boolean
}) {
  const isUser = msg.role === 'user'
  const content = msg.content

  const textContent =
    typeof content === 'string'
      ? content
      : content
          .filter((p): p is TextPart => p.type === 'text')
          .map(p => p.text)
          .join('')

  const images =
    typeof content !== 'string'
      ? content.filter((p): p is ImagePart => p.type === 'image_url')
      : []

  return (
    <div className={`flex gap-3 mb-5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div
        className={`w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold mt-0.5
          ${isUser ? 'bg-gray-200 text-gray-600' : 'bg-brand-600 text-white'}`}
      >
        {isUser ? 'Tú' : 'AI'}
      </div>

      {/* Content */}
      <div className={`flex flex-col gap-2 min-w-0 max-w-[78%] ${isUser ? 'items-end' : 'items-start'}`}>
        {/* Images */}
        {images.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {images.map((img, i) => (
              <img
                key={i}
                src={img.image_url.url}
                alt="adjunto"
                className="max-h-52 max-w-xs rounded-xl object-cover border border-gray-200"
              />
            ))}
          </div>
        )}

        {/* Text bubble */}
        {(textContent || isStreaming) && (
          <div
            className={`px-4 py-3 rounded-2xl text-sm leading-relaxed break-words
              ${isUser
                ? 'bg-brand-600 text-white rounded-tr-sm'
                : 'bg-white text-gray-800 shadow-sm border border-gray-100 rounded-tl-sm'
              }`}
          >
            {renderContent(textContent)}
            {isStreaming && (
              <span className="inline-block w-0.5 h-[1em] bg-current ml-0.5 align-middle animate-pulse" />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Chat Page ─────────────────────────────────────────────────────────────────

const FALLBACK_MODELS = ['claude-sonnet-4-6', 'claude-opus-4-6', 'gpt-4o']
const DEFAULT_MODEL = FALLBACK_MODELS[0]

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [images, setImages] = useState<string[]>([])
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const bottomRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const { data: activeModels } = useQuery({
    queryKey: ['models', 'active'],
    queryFn: modelsApi.getActive,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
  const { data: registry } = useQuery({
    queryKey: ['providers'],
    queryFn: providersApi.list,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
  const activeProvider = registry?.providers.find(p => p.id === registry.active_provider_id)

  // Alias fijo que litellm enruta al modelo real del proveedor activo
  const model = (activeModels && activeModels.length > 0)
    ? activeModels[0].model_name
    : DEFAULT_MODEL

  // Scroll to bottom when messages update
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Auto-resize textarea
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`
  }, [input])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text && images.length === 0) return
    if (streaming) return
    setError(null)

    const content: string | ContentPart[] =
      images.length > 0
        ? [
            ...images.map(url => ({
              type: 'image_url' as const,
              image_url: { url },
            })),
            ...(text ? [{ type: 'text' as const, text }] : []),
          ]
        : text

    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', content }
    const asstMsg: Message = { id: crypto.randomUUID(), role: 'assistant', content: '' }

    const history = [...messages, userMsg]
    setMessages([...history, asstMsg])
    setInput('')
    setImages([])
    setStreaming(true)

    const ctrl = new AbortController()
    abortRef.current = ctrl

    try {
      for await (const chunk of streamChat(history, model, ctrl.signal)) {
        setMessages(prev => {
          const last = prev[prev.length - 1]
          return [
            ...prev.slice(0, -1),
            { ...last, content: (last.content as string) + chunk },
          ]
        })
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      setError(String(e))
      setMessages(prev => {
        const last = prev[prev.length - 1]
        return last.content === '' ? prev.slice(0, -1) : prev
      })
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, images, messages, model, streaming])

  const stop = () => abortRef.current?.abort()

  const clear = () => {
    stop()
    setMessages([])
    setImages([])
    setError(null)
  }

  const handleFiles = (files: FileList | null) => {
    if (!files) return
    Array.from(files).forEach(f => {
      if (!f.type.startsWith('image/')) return
      const r = new FileReader()
      r.onload = e => setImages(prev => [...prev, e.target!.result as string])
      r.readAsDataURL(f)
    })
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* ── Header ── */}
      <div className="flex items-center justify-between px-1 py-3 border-b border-gray-200 bg-white flex-shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <h1 className="text-sm font-semibold text-gray-700 flex-shrink-0">Chat</h1>
          {activeProvider && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-gray-100 text-xs text-gray-500 min-w-0">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0" />
              <span className="font-medium text-gray-700 flex-shrink-0">{activeProvider.name}</span>
              {activeProvider.active_model && (
                <>
                  <span className="text-gray-300 flex-shrink-0">·</span>
                  <span className="truncate max-w-[160px]">{activeProvider.active_model}</span>
                </>
              )}
            </div>
          )}
        </div>
        <button
          onClick={clear}
          className="text-xs text-gray-400 hover:text-gray-600 px-2.5 py-1.5 rounded-lg hover:bg-gray-100 transition-colors flex-shrink-0"
        >
          Limpiar
        </button>
      </div>

      {/* ── Messages ── */}
      <div className="flex-1 overflow-y-auto px-1 py-6 bg-gray-50">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
            <div className="w-14 h-14 rounded-2xl bg-brand-600 flex items-center justify-center">
              <svg
                className="w-7 h-7 text-white"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                />
              </svg>
            </div>
            <p className="text-sm text-gray-500">
              {activeProvider
                ? <>Chatea con <span className="font-medium text-gray-700">{activeProvider.name}</span></>
                : 'Inicia una conversación'}
            </p>
            <p className="text-xs text-gray-400">Enter para enviar · Shift+Enter para nueva línea</p>
          </div>
        ) : (
          messages.map((msg, idx) => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              isStreaming={
                streaming &&
                idx === messages.length - 1 &&
                msg.role === 'assistant'
              }
            />
          ))
        )}
        {error && (
          <div className="mx-auto max-w-lg bg-red-50 border border-red-100 rounded-xl px-4 py-3 text-xs text-red-600 mb-4">
            {error}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── Input ── */}
      <div className="flex-shrink-0 bg-white border-t border-gray-200 px-1 py-3">
        {/* Image previews */}
        {images.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {images.map((url, i) => (
              <div key={i} className="relative group">
                <img
                  src={url}
                  alt=""
                  className="h-14 w-14 object-cover rounded-lg border border-gray-200"
                />
                <button
                  onClick={() => setImages(p => p.filter((_, j) => j !== i))}
                  className="absolute -top-1 -right-1 w-4 h-4 bg-gray-700 text-white rounded-full text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity leading-none"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2">
          {/* Image attach */}
          <button
            onClick={() => fileRef.current?.click()}
            title="Adjuntar imagen"
            className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
              />
            </svg>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={e => {
              handleFiles(e.target.files)
              e.target.value = ''
            }}
          />

          {/* Textarea */}
          <textarea
            ref={taRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            disabled={streaming}
            placeholder="Escribe un mensaje…"
            rows={1}
            className="flex-1 resize-none rounded-xl border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:bg-gray-50 disabled:text-gray-400"
          />

          {/* Stop / Send */}
          {streaming ? (
            <button
              onClick={stop}
              title="Detener"
              className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-red-500 hover:bg-red-600 text-white transition-colors"
            >
              <svg
                className="w-3.5 h-3.5"
                fill="currentColor"
                viewBox="0 0 16 16"
              >
                <rect x="3" y="3" width="10" height="10" rx="1.5" />
              </svg>
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!input.trim() && images.length === 0}
              title="Enviar (Enter)"
              className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <svg
                className="w-4 h-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
