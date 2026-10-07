import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { QualityGateSettings } from './QualityGateSettings'

const delegation = { enabled: true, workspace_allowlist: [], tier_order: {}, max_parallel_jobs: 3, max_attempts: 3, job_retention: 200,
  thinkers: ['claude', 'codex'], account_exhausted_pct: 98, allow_request_verify: false, review_default: true, verify_timeout_s: 600, review_timeout_s: 900 }

describe('QualityGateSettings', () => {
  it('edits thinkers and flags', () => {
    const onChange = vi.fn()
    render(<QualityGateSettings delegation={delegation} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText(/Pensadores/), { target: { value: 'claude, claude-2, codex' } })
    expect(onChange).toHaveBeenCalledWith({ thinkers: ['claude', 'claude-2', 'codex'] })
    fireEvent.click(screen.getByLabelText(/Permitir comandos de verificación/))
    expect(onChange).toHaveBeenCalledWith({ allow_request_verify: true })
  })
})
