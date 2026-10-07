import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Agents } from './Agents'

const saveMutate = vi.fn()

vi.mock('@/hooks/useSmart', () => ({
  useSmartConfig: () => ({ data: {
    delegation: { enabled: true, workspace_allowlist: ['C:\\\\work'], tier_order: { trivial: [], simple: [], standard: [], complex: [] }, max_parallel_jobs: 3, max_attempts: 3, job_retention: 200, thinkers: ['claude'], account_exhausted_pct: 98, allow_request_verify: true, review_default: true, verify_timeout_s: 600, review_timeout_s: 900 },
    cli_agents: [{ id: 'claude', name: 'Claude Code', enabled: true, default_model: 'sonnet', timeout_s: 600, max_credits: 0, quota_reset: '5h', model_by_tier: {}, alt_model_on_quota: '', exe_path: '', supported_tiers: [], agentic: true, priority: 1, cost_weight: 1, max_concurrency: 1, cooldown_s: 60, extra_args: [] }],
  }, isLoading: false }),
  useAgents: () => ({ data: { agents: [{ id: 'claude', installed: true, version: '1.0', auth: 'ok', state: 'available', seconds_left: 0, quota: {}, default_model: 'sonnet', exe: '', last_signal: '', last_excerpt: '', running: 0, checked_at: '', error: '' }] } }),
  useSaveSmartConfig: () => ({ mutate: saveMutate, isPending: false }),
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

describe('Agents', () => {
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
    expect(saveMutate).toHaveBeenCalled()
    const body = saveMutate.mock.calls[0][0] as { delegation: { thinkers: string[]; allow_request_verify: boolean; max_parallel_jobs: number } }
    expect(body.delegation.thinkers).toEqual(['claude'])
    expect(body.delegation.allow_request_verify).toBe(true)
    expect(body.delegation.max_parallel_jobs).toBe(5)
  })
})
