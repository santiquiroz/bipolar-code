import { describe, expect, it } from 'vitest'
import { nextPoolVarName } from './useCredentials'

describe('nextPoolVarName', () => {
  it('starts at _2 and skips used names', () => {
    expect(nextPoolVarName('ANTHROPIC_API_KEY', [])).toBe('ANTHROPIC_API_KEY_2')
    expect(nextPoolVarName('ANTHROPIC_API_KEY', ['ANTHROPIC_API_KEY_2', 'ANTHROPIC_API_KEY_4'])).toBe('ANTHROPIC_API_KEY_3')
  })
})
