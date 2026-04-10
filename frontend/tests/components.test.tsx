import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Badge } from '@/components/Badge'
import { Card } from '@/components/Card'
import { Button } from '@/components/Button'

describe('Badge', () => {
  it('renders label', () => {
    render(<Badge label="healthy" variant="success" />)
    expect(screen.getByText('healthy')).toBeInTheDocument()
  })
})

describe('Card', () => {
  it('renders title and children', () => {
    render(<Card title="Test Title"><p>Content</p></Card>)
    expect(screen.getByText('Test Title')).toBeInTheDocument()
    expect(screen.getByText('Content')).toBeInTheDocument()
  })
})

describe('Button', () => {
  it('shows spinner when loading', () => {
    const { container } = render(<Button loading>Click</Button>)
    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('is disabled when loading', () => {
    render(<Button loading>Click</Button>)
    expect(screen.getByRole('button')).toBeDisabled()
  })
})
