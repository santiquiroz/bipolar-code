import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CredentialPoolPanel } from './CredentialPoolPanel'

const add = vi.fn()
vi.mock('@/hooks/useCredentials', () => ({
  useProviderCredentials: () => ({ data: { provider_id: 'anthropic', missing: ['ANTHROPIC_API_KEY_3'], slots: [
    { slot: 0, env_var: 'ANTHROPIC_API_KEY', has_value: true, health_key: 'provider:anthropic#ANTHROPIC_API_KEY', state: 'available', seconds_left: 0, last_signal: '' },
    { slot: 1, env_var: 'ANTHROPIC_API_KEY_2', has_value: true, health_key: 'provider:anthropic#ANTHROPIC_API_KEY_2', state: 'cooling', seconds_left: 125, last_signal: 'rate_limit' },
  ] } }),
  useAddPoolKey: () => ({ mutate: add, isPending: false }),
  useRemovePoolKey: () => ({ mutate: vi.fn(), isPending: false }),
}))

const provider = { id: 'anthropic', name: 'Anthropic', auth_env_var: 'ANTHROPIC_API_KEY', extra_auth_env_vars: ['ANTHROPIC_API_KEY_2', 'ANTHROPIC_API_KEY_3'] } as never

describe('CredentialPoolPanel', () => {
  it('lists slots, missing vars and never shows values', () => {
    render(<CredentialPoolPanel provider={provider} />)
    expect(screen.getByText('ANTHROPIC_API_KEY_2')).toBeInTheDocument()
    expect(screen.getByText('cooling')).toBeInTheDocument()
    expect(screen.getByText(/sin valor en .env/)).toBeInTheDocument()
  })

  it('disables add with an empty value and clears after adding', () => {
    render(<CredentialPoolPanel provider={provider} />)
    const button = screen.getByRole('button', { name: 'Agregar' })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Nueva llave'), { target: { value: '   ' } })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Nueva llave'), { target: { value: 'sk-123' } })
    fireEvent.click(button)
    expect(add).toHaveBeenCalledWith({ value: 'sk-123' }, expect.anything())
  })
})
