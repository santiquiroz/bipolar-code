import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Agents } from './Agents'

vi.mock('@/hooks/useSmart', () => ({
  useSmartConfig: () => ({ data: {
    delegation: { enabled: true, workspace_allowlist: ['C:\\\\work'], tier_order: { trivial: [], simple: [], standard: [], complex: [] }, max_parallel_jobs: 3, max_attempts: 3, job_retention: 200 },
    cli_agents: [{ id: 'claude', name: 'Claude Code', enabled: true, default_model: 'sonnet', timeout_s: 600, max_credits: 0, quota_reset: '5h', model_by_tier: {}, alt_model_on_quota: '', exe_path: '', supported_tiers: [], agentic: true, priority: 1, cost_weight: 1, max_concurrency: 1, cooldown_s: 60, extra_args: [] }],
  }, isLoading: false }),
  useAgents: () => ({ data: { agents: [{ id: 'claude', installed: true, version: '1.0', auth: 'ok', state: 'available', seconds_left: 0, quota: {}, default_model: 'sonnet', exe: '', last_signal: '', last_excerpt: '', running: 0, checked_at: '', error: '' }] } }),
  useSaveSmartConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useProbeAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useResetHealth: () => ({ mutate: vi.fn() }),
  useClassify: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('@/hooks/useDelegate', () => ({
  useJobs: () => ({ data: { jobs: [] } }),
  useSubmitJob: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelJob: () => ({ mutate: vi.fn() }),
}))

describe('Agents', () => {
  it('renders agent rows and delegation switch', () => {
    render(<Agents />)
    expect(screen.getAllByText('Claude Code').length).toBeGreaterThan(0)
    expect(screen.getByText('Delegación a agentes CLI')).toBeInTheDocument()
  })
})
