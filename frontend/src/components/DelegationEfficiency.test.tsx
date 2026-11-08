import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DelegationEfficiency } from './DelegationEfficiency'

const h = vi.hoisted(() => ({ state: {} as Record<string, unknown>, refetch: vi.fn() }))
vi.mock('@/hooks/useDelegate', () => ({
  useDelegationEfficiency: () => ({ ...h.state, refetch: h.refetch }),
}))

const data = {
  days: 30, since: '2026-10-08T00:00:00Z',
  totals: { jobs: 10, succeeded: 8, success_pct: 80, cost_usd: 1.5, tokens_in: 1, tokens_out: 1, anthropic_share_pct: 62.5 },
  pools: [
    { pool: 'anthropic_account', label: 'Cuenta Anthropic', jobs: 5, succeeded: 5, cost_usd: 0 },
    { pool: 'free', label: 'Gratis', jobs: 5, succeeded: 3, cost_usd: 0 },
  ],
  agents: [{ agent_id: 'claude', name: 'Claude Code', pool: 'anthropic_account', jobs: 5, succeeded: 5, success_pct: 100, statuses: {}, accept_pct: 90, cost_usd: null, tokens_in: 1, tokens_out: 1, cost_per_success: null, models: ['sonnet', 'opus'] }],
  warnings: [{ level: 'high', code: 'share', message: 'Claude hace casi todo', agent_id: null }],
}

describe('DelegationEfficiency', () => {
  beforeEach(() => { h.refetch.mockReset(); h.state = { data, isLoading: false, isError: false } })

  it('muestra avisos con nivel, KPIs y leyenda con porcentajes', () => {
    render(<DelegationEfficiency />)
    expect(screen.getByText('Claude hace casi todo')).toBeInTheDocument()
    expect(screen.getByText('alto')).toHaveAttribute('data-variant', 'error')
    expect(screen.getAllByText('Jobs').length).toBeGreaterThan(0)
    expect(screen.getByText('80.0 %')).toBeInTheDocument()
    expect(screen.getByText('62.5 %')).toHaveClass('text-amber-600')
    expect(screen.getByText('US$1.50')).toBeInTheDocument()
    expect(screen.getByText(/Cuenta Anthropic: 5 éxitos \(62\.5 %\)/)).toBeInTheDocument()
    expect(screen.getByText(/Gratis: 3 éxitos \(37\.5 %\)/)).toBeInTheDocument()
    expect(screen.getByText('sonnet, opus')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '30 días' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('muestra el estado vacío', () => {
    h.state = { data: { ...data, totals: { ...data.totals, jobs: 0 } }, isLoading: false, isError: false }
    render(<DelegationEfficiency />)
    expect(screen.getByText(/Todavía no hay jobs en esta ventana/)).toBeInTheDocument()
  })

  it('muestra el error y reintenta', () => {
    h.state = { data: undefined, isLoading: false, isError: true }
    render(<DelegationEfficiency />)
    expect(screen.getByText('No se pudo cargar la eficiencia')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))
    expect(h.refetch).toHaveBeenCalled()
  })
})
