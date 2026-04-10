import type { ReactNode } from 'react'

interface CardProps {
  title?: string
  children: ReactNode
  className?: string
}

export function Card({ title, children, className = '' }: CardProps) {
  return (
    <div className={`bg-white rounded-2xl shadow-sm border border-gray-100 p-6 ${className}`}>
      {title && <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">{title}</h3>}
      {children}
    </div>
  )
}
