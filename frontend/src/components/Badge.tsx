type Variant = 'success' | 'error' | 'warning' | 'info' | 'neutral'

const styles: Record<Variant, string> = {
  success: 'bg-emerald-100 text-emerald-700',
  error:   'bg-red-100 text-red-700',
  warning: 'bg-amber-100 text-amber-700',
  info:    'bg-blue-100 text-blue-700',
  neutral: 'bg-gray-100 text-gray-600',
}

interface BadgeProps {
  label: string
  variant?: Variant
}

export function Badge({ label, variant = 'neutral' }: BadgeProps) {
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${styles[variant]}`}>
      {label}
    </span>
  )
}
