import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { Spinner } from './Spinner'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'outline'
type Size = 'sm' | 'md' | 'lg' | 'icon'

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-gradient-brand text-white shadow-glow-sm hover:shadow-glow hover:brightness-110 active:brightness-95',
  secondary:
    'bg-slate-200 text-slate-900 border border-slate-200 hover:bg-slate-400/80 active:bg-slate-200',
  ghost: 'text-slate-600 hover:bg-slate-100 hover:text-white active:bg-white/10',
  danger:
    'bg-rose-600/90 text-white hover:bg-rose-600 active:bg-rose-700 shadow-sm',
  success:
    'bg-emerald-600/90 text-white hover:bg-emerald-600 active:bg-emerald-700 shadow-sm',
  outline:
    'border border-brand-500/40 text-brand-600 hover:bg-brand-500/10 hover:border-brand-500/60 active:bg-brand-50',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5 rounded-lg',
  md: 'h-9 px-4 text-sm gap-2 rounded-lg',
  lg: 'h-11 px-6 text-base gap-2 rounded-xl',
  icon: 'h-9 w-9 rounded-lg',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  leftIcon?: ReactNode
  rightIcon?: ReactNode
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  leftIcon,
  rightIcon,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center font-medium transition-all duration-200 select-none whitespace-nowrap shrink-0',
        'disabled:opacity-40 disabled:pointer-events-none',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <Spinner size={size === 'lg' ? 18 : 14} /> : leftIcon}
      {children}
      {!loading && rightIcon}
    </button>
  )
}
