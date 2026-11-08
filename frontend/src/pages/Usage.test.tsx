import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Usage } from './Usage'

vi.mock('recharts', () => {
  const Stub = () => null
  return { BarChart: Stub, Bar: Stub, XAxis: Stub, YAxis: Stub, Tooltip: Stub, Legend: Stub, ResponsiveContainer: Stub }
})
vi.mock('@/hooks/useUsage', () => ({
  useUsageSummary: () => ({ data: { by_provider: { 'cli:muse': { requests: 1, cost_usd: 0 }, openrouter: { requests: 1, cost_usd: 0 } }, series: [] }, isLoading: false }),
  useUsageHistory: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/hooks/useSmart', () => ({
  useDecisions: () => ({ data: { decisions: [] } }),
  useDecisionsSummary: () => ({ data: undefined }),
  useSmartHealth: () => ({ data: { targets: {} } }),
  useResetHealth: () => ({ mutate: vi.fn() }),
}))

describe('Usage', () => {
  it('las tarjetas de routing y salud no están dentro del encabezado', () => {
    render(<Usage />)
    const title = screen.getByRole('heading', { name: 'Uso y Costos' })
    const header = title.parentElement as HTMLElement
    expect(header).toContainElement(screen.getByRole('button', { name: 'Hoy' }))
    expect(header).not.toContainElement(screen.getByText('Decisiones de routing'))
    expect(header).not.toContainElement(screen.getByText('Salud de destinos'))
  })

  it('distingue suscripción de gratis en el costo', () => {
    render(<Usage />)
    expect(screen.getByText('suscripción')).toBeInTheDocument()
    expect(screen.getByText('GRATIS')).toBeInTheDocument()
  })
})
