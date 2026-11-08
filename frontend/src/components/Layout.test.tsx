import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Layout } from './Layout'

vi.mock('@/hooks/useHealth', () => ({ useHealth: () => ({ data: { version: '2.30.0' } }) }))

describe('Layout', () => {
  it('abre el menú móvil y Escape lo cierra', () => {
    render(<MemoryRouter><Layout /></MemoryRouter>)
    const open = screen.getByRole('button', { name: 'Abrir menú' })
    expect(open).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Cerrar menú' })).not.toBeInTheDocument()
    fireEvent.click(open)
    expect(screen.getByRole('button', { name: 'Cerrar menú' })).toBeInTheDocument()
    expect(open).toHaveAttribute('aria-expanded', 'true')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('button', { name: 'Cerrar menú' })).not.toBeInTheDocument()
  })

  it('muestra la versión y el enlace para saltar al contenido', () => {
    render(<MemoryRouter><Layout /></MemoryRouter>)
    expect(screen.getAllByText('v2.30.0').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'Saltar al contenido' })).toHaveAttribute('href', '#contenido')
  })
})
