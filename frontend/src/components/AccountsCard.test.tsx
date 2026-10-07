import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AccountsCard } from './AccountsCard'

const create = vi.fn((_body, opts) => opts?.onSuccess?.({ agent: { id: 'claude-2' }, login: { powershell: "$env:CLAUDE_CONFIG_DIR='C:/a'; claude", bash: "CLAUDE_CONFIG_DIR='C:/a' claude", note: 'ejecuta /login' } }))

vi.mock('@/hooks/useAccounts', () => ({
  useAccounts: () => ({ data: { accounts: [
    { agent_id: 'claude-2', base: 'claude', label: 'Personal 2', account_dir: 'C:/a', enabled: true, has_login: true, state: 'available', seconds_left: 0,
      usage: { received_at: 't', five_hour: { used_percentage: 42.4, resets_at: 1 }, seven_day: { used_percentage: 13, resets_at: 2 } } },
    { agent_id: 'codex-2', base: 'codex', label: 'Codex 2', account_dir: 'C:/b', enabled: false, has_login: false, state: 'available', seconds_left: 0, usage: null },
  ] } }),
  useCreateAccount: () => ({ mutate: create, isPending: false }),
  useDeleteAccount: () => ({ mutate: vi.fn(), isPending: false }),
}))

describe('AccountsCard', () => {
  it('shows usage bars or the no-usage text', () => {
    render(<AccountsCard />)
    expect(screen.getByText('Personal 2')).toBeInTheDocument()
    expect(screen.getByText(/5 h: 42 %/)).toBeInTheDocument()
    expect(screen.getByText(/7 días: 13 %/)).toBeInTheDocument()
    expect(screen.getByText('sin datos de uso')).toBeInTheDocument()
    expect(screen.getByText('Sin login')).toBeInTheDocument()
  })

  it('keeps login commands visible after creating', () => {
    render(<AccountsCard />)
    fireEvent.change(screen.getByLabelText('Etiqueta'), { target: { value: 'Personal 3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear' }))
    expect(screen.getByText("$env:CLAUDE_CONFIG_DIR='C:/a'; claude")).toBeInTheDocument()
    expect(screen.getByText("CLAUDE_CONFIG_DIR='C:/a' claude")).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Copiar' })).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Listo' }))
    expect(screen.queryByText("CLAUDE_CONFIG_DIR='C:/a' claude")).not.toBeInTheDocument()
  })
})
