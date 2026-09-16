import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

type Variant = 'gray' | 'blue' | 'green' | 'red' | 'purple' | 'orange' | 'indigo' | 'amber'

const VARIANTS: Record<Variant, string> = {
  gray: 'bg-slate-100 text-slate-600 border-slate-200',
  blue: 'bg-sky-50 text-sky-600 border-sky-200',
  green: 'bg-emerald-50 text-emerald-600 border-emerald-200',
  red: 'bg-rose-50 text-rose-600 border-rose-200',
  purple: 'bg-brand-50 text-brand-600 border-brand-200',
  orange: 'bg-orange-50 text-orange-600 border-orange-200',
  indigo: 'bg-indigo-50 text-indigo-600 border-indigo-200',
  amber: 'bg-amber-50 text-amber-600 border-amber-200',
}

interface BadgeProps {
  variant?: Variant
  size?: 'sm' | 'md'
  dot?: boolean
  children: ReactNode
  className?: string
}

export function Badge({ variant = 'gray', size = 'sm', dot = false, children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 font-medium rounded-full border',
        size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs',
        VARIANTS[variant],
        className,
      )}
    >
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current opacity-80" />}
      {children}
    </span>
  )
}
