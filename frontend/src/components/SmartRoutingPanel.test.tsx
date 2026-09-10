import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SmartRoutingPanel } from './SmartRoutingPanel'

const { presetDefault } = vi.hoisted(() => ({ presetDefault: vi.fn().mockResolvedValue({ tiers: [], saved: false }) }))
vi.mock('@/hooks/useSmart', () => ({
  useSmartConfig: () => ({ data: {
    smart: { enabled: false, mode: 'shadow', thresholds: { simple: 25, standard: 50, complex: 75 }, tiers: [], budgets: [], honor_tier_header: true, sticky_tool_loops: true, sticky_ttl_seconds: 1800, skip_cooling_providers: true, respect_capabilities: true },
    recommendations: [],
  }, isLoading: false }),
  useSaveSmartConfig: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useExplain: () => ({ mutate: vi.fn(), isPending: false, data: undefined }),
}))
vi.mock('@/services/api', () => ({
  smartApi: { presetDefault },
  providersApi: { update: vi.fn() },
}))

describe('SmartRoutingPanel', () => {
  beforeEach(() => presetDefault.mockClear())
  it('renders four tier rows', () => {
    render(<SmartRoutingPanel providers={[]} />)
    expect(screen.getAllByText(/trivial|simple|standard|complex/).length).toBeGreaterThanOrEqual(4)
  })
  it('marks the form dirty when mode changes', () => {
    render(<SmartRoutingPanel providers={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Activo' }))
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeInTheDocument()
  })
  it('calls the default preset when suggesting', async () => {
    render(<SmartRoutingPanel providers={[]} />)
    fireEvent.click(screen.getAllByRole('button', { name: 'Sugerir' })[0])
    await waitFor(() => expect(presetDefault).toHaveBeenCalled())
  })
})
