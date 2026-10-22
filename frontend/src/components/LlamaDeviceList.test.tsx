import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LlamaDeviceList } from './LlamaDeviceList'
import type { LlamaDevice } from '@/types/provider'

const devices: LlamaDevice[] = [
  { id: 'Vulkan0', index: 0, backend: 'Vulkan', name: 'AMD RX 7800 XT', integrated: false, vram_total_mib: 16384, vram_free_mib: 12000 },
  { id: 'Vulkan1', index: 1, backend: 'Vulkan', name: 'AMD Radeon Graphics', integrated: true, vram_total_mib: 8192, vram_free_mib: 4000 },
  { id: 'Vulkan2', index: 2, backend: 'Vulkan', name: 'NVIDIA RTX 3060', integrated: false, vram_total_mib: 12288, vram_free_mib: 9000 },
]

describe('LlamaDeviceList', () => {
  it('renders every device and marks the integrated one', () => {
    render(<LlamaDeviceList devices={devices} value="auto" onChange={() => {}} />)
    expect(screen.getByText('AMD RX 7800 XT')).toBeInTheDocument()
    expect(screen.getByText('AMD Radeon Graphics')).toBeInTheDocument()
    expect(screen.getByText('NVIDIA RTX 3060')).toBeInTheDocument()
    expect(screen.getByText('integrada')).toBeInTheDocument()
  })

  it('excludes the integrated device in auto mode', () => {
    render(<LlamaDeviceList devices={devices} value="auto" onChange={() => {}} />)
    expect(screen.getByRole('checkbox', { name: 'Automático (sin integradas)' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'AMD RX 7800 XT' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'NVIDIA RTX 3060' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'AMD Radeon Graphics' })).not.toBeChecked()
  })

  it('calls onChange with the checked list when toggling a device', () => {
    const onChange = vi.fn()
    render(<LlamaDeviceList devices={devices} value={['Vulkan0', 'Vulkan2']} onChange={onChange} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'AMD Radeon Graphics' }))
    expect(onChange).toHaveBeenCalledWith(['Vulkan0', 'Vulkan2', 'Vulkan1'])
    fireEvent.click(screen.getByRole('checkbox', { name: 'AMD RX 7800 XT' }))
    expect(onChange).toHaveBeenCalledWith(['Vulkan2'])
  })

  it('reorders the list when moving a device up', () => {
    const onChange = vi.fn()
    render(<LlamaDeviceList devices={devices} value={['Vulkan0', 'Vulkan2']} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Subir NVIDIA RTX 3060' }))
    expect(onChange).toHaveBeenCalledWith(['Vulkan2', 'Vulkan0'])
  })

  it('switches between auto and manual with the toggle', () => {
    const onChange = vi.fn()
    const { rerender } = render(<LlamaDeviceList devices={devices} value="auto" onChange={onChange} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Automático (sin integradas)' }))
    expect(onChange).toHaveBeenCalledWith(['Vulkan0', 'Vulkan2'])
    rerender(<LlamaDeviceList devices={devices} value={['Vulkan0', 'Vulkan2']} onChange={onChange} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Automático (sin integradas)' }))
    expect(onChange).toHaveBeenCalledWith('auto')
  })
})
