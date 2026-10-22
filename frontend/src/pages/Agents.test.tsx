import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Agents } from './Agents'

const h = vi.hoisted(() => {
  const saveMutate = vi.fn()
  const baseConfig = {
    delegation: { enabled: true, workspace_allowlist: ['C:\\work'], tier_order: { trivial: [], simple: [], standard: [], complex: [] }, max_parallel_jobs: 3, max_attempts: 3, job_retention: 200, thinkers: ['claude'], account_exhausted_pct: 98, allow_request_verify: true, review_default: true, verify_timeout_s: 600, review_timeout_s: 900 },
    cli_agents: [{ id: 'claude', name: 'Claude Code', enabled: true, default_model: 'sonnet', timeout_s: 600, max_credits: 0, max_monthly_usd: 200, quota_reset: '5h', model_by_tier: {}, alt_model_on_quota: '', exe_path: '', supported_tiers: [], agentic: true, priority: 1, cost_weight: 1, max_concurrency: 1, cooldown_s: 60, extra_args: [] }],
  }
  let config = baseConfig
  return {
    saveMutate,
    getConfig: () => config,
    setConfig: (c: typeof baseConfig) => { config = c },
    resetConfig: () => { config = baseConfig },
  }
})
vi.mock('@/hooks/useSmart', () => ({
  useSmartConfig: () => ({ data: h.getConfig(), isLoading: false }),
  useAgents: () => ({ data: { agents: [{ id: 'claude', installed: true, version: '1.0', auth: 'ok', state: 'available', seconds_left: 0, quota: { month_spend_usd: 12.5, max_monthly_usd: 200 }, default_model: 'sonnet', exe: '', last_signal: '', last_excerpt: '', running: 0, checked_at: '', error: '' }] } }),
  useSaveSmartConfig: () => ({ mutate: h.saveMutate, isPending: false }),
  useProbeAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useResetHealth: () => ({ mutate: vi.fn() }),
  useClassify: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useAccounts', () => ({
  useAccounts: () => ({ data: { accounts: [] } }),
  useCreateAccount: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAccount: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useDelegate', () => ({
  useJobs: () => ({ data: { jobs: [{
    id: 'job-1', created_at: '', status: 'succeeded', mode: 'task', workspace: 'C:\\work', task_preview: 'tarea',
    tier: 'simple', score: 1, reasons: [], skipped: [], agent_id: 'claude', model: 'sonnet', attempts: [],
    output_tail: '', files_touched: ['a.ts', 'b.ts'], error: '', tokens_in: 0, tokens_out: 0, cost_usd: null,
    verification_status: 'passed', review_status: 'skipped', escalations: 1, log_path: '',
  }] } }),
  useSubmitJob: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelJob: () => ({ mutate: vi.fn() }),
}))

const agentCheckbox = () => screen.getAllByLabelText(/habilitado/i)[0] as HTMLInputElement
const okOnSave = (body: { cli_agents: unknown; delegation: unknown }, opts?: { onSuccess?: (data: unknown) => void }) =>
  opts?.onSuccess?.({ cli_agents: body.cli_agents, delegation: body.delegation })
const withClaudeDisabled = () => {
  const base = h.getConfig()
  h.setConfig({ ...base, cli_agents: base.cli_agents.map(a => ({ ...a, enabled: false })) })
}

describe('Agents', () => {
  beforeEach(() => {
    h.resetConfig()
    h.saveMutate.mockReset()
    h.saveMutate.mockImplementation(okOnSave)
  })
  it('renders agent rows and delegation switch', () => {
    render(<Agents />)
    expect(screen.getAllByText('Claude Code').length).toBeGreaterThan(0)
    expect(screen.getByText('Delegación a agentes CLI')).toBeInTheDocument()
  })

  it('shows gate badges with warning variant for skipped', () => {
    render(<Agents />)
    expect(screen.getByText('Verificación')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Revisión' })).toBeInTheDocument()
    expect(screen.getByText('Escalados')).toBeInTheDocument()
    expect(screen.getByText('passed')).toBeInTheDocument()
    expect(screen.getByText('skipped')).toHaveAttribute('data-variant', 'warning')
  })

  it('keeps new delegation fields when saving', () => {
    render(<Agents />)
    fireEvent.change(screen.getByLabelText(/Máximo paralelo/), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(h.saveMutate).toHaveBeenCalled()
    const body = h.saveMutate.mock.calls[0][0] as { delegation: { thinkers: string[]; allow_request_verify: boolean; max_parallel_jobs: number } }
    expect(body.delegation.thinkers).toEqual(['claude'])
    expect(body.delegation.allow_request_verify).toBe(true)
    expect(body.delegation.max_parallel_jobs).toBe(5)
  })
  it('saves the monthly limit and shows monthly spend', () => {
    h.saveMutate.mockClear()
    render(<Agents />)
    expect(screen.getByText('Gasto del mes: $12.50 / $200.00')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Tope mensual USD de Claude Code'), { target: { value: '100' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(h.saveMutate.mock.calls[0][0].cli_agents[0].max_monthly_usd).toBe(100)
  })


  it('muestra la barra al marcar habilitado', () => {
    withClaudeDisabled()
    render(<Agents />)
    expect(screen.queryByText('Cambios sin guardar')).not.toBeInTheDocument()
    fireEvent.click(agentCheckbox())
    expect(screen.getByText('Cambios sin guardar')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Descartar' })).toBeInTheDocument()
  })
  it('Guardar envía cli_agents con enabled: true', async () => {
    withClaudeDisabled()
    render(<Agents />)
    fireEvent.click(agentCheckbox())
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(h.saveMutate).toHaveBeenCalledTimes(1)
    const body = h.saveMutate.mock.calls[0][0]
    expect(body.cli_agents.find((a: { id: string }) => a.id === 'claude').enabled).toBe(true)
    await waitFor(() => expect(screen.queryByText('Cambios sin guardar')).not.toBeInTheDocument())
  })
  it('un PUT con error muestra el detalle y conserva la barra', () => {
    withClaudeDisabled()
    h.saveMutate.mockImplementation((_body, opts) =>
      opts?.onError?.({ response: { data: { detail: 'claude: timeout_s fuera de 60..3600' } } }))
    render(<Agents />)
    fireEvent.click(agentCheckbox())
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    const detail = screen.getByText('claude: timeout_s fuera de 60..3600')
    expect(detail).toHaveClass('text-red-600')
    expect(screen.getByText('Cambios sin guardar')).toBeInTheDocument()
    expect(agentCheckbox()).toBeChecked()
  })
  it('Descartar restaura el estado del servidor', () => {
    withClaudeDisabled()
    render(<Agents />)
    fireEvent.click(agentCheckbox())
    expect(agentCheckbox()).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }))
    expect(screen.queryByText('Cambios sin guardar')).not.toBeInTheDocument()
    expect(agentCheckbox()).not.toBeChecked()
  })
})
