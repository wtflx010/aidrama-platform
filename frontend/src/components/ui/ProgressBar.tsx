import { cn } from '../../lib/cn'

interface ProgressBarProps {
  value: number
  label?: string
  variant?: 'brand' | 'blue' | 'green'
  className?: string
}

const VARIANTS = {
  brand: 'bg-gradient-brand',
  blue: 'bg-sky-500',
  green: 'bg-emerald-500',
}

export function ProgressBar({ value, label, variant = 'brand', className }: ProgressBarProps) {
  return (
    <div className={cn('w-full', className)}>
      {label && <div className="text-xs text-slate-500 mb-1.5">{label}</div>}
      <div className="w-full h-2 bg-slate-200 rounded-full overflow-hidden">
        <div
          className={cn('h-full rounded-full transition-all duration-500 ease-out', VARIANTS[variant])}
          style={{ width: `${Math.max(3, Math.min(100, value))}%` }}
        />
      </div>
    </div>
  )
}
