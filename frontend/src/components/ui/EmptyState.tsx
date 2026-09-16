import type { ReactNode } from 'react'
import { Icon } from '../../lib/icons'

interface EmptyStateProps {
  icon?: string
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ icon = 'sparkles', title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center animate-fade-in">
      <div className="relative mb-4">
        <div className="absolute inset-0 bg-brand-500/20 blur-2xl rounded-full" />
        <div className="relative w-16 h-16 rounded-2xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center">
          <Icon name={icon} size={28} className="text-brand-600" />
        </div>
      </div>
      <h3 className="text-base font-medium text-slate-800 mb-1">{title}</h3>
      {description && (
        <p className="text-sm text-slate-400 max-w-sm whitespace-pre-line">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
