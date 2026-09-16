import { Icon } from '../../lib/icons'
import { cn } from '../../lib/cn'

interface SpinnerProps {
  size?: number
  className?: string
}

export function Spinner({ size = 16, className }: SpinnerProps) {
  return <Icon name="loader-2" size={size} className={cn('animate-spin', className)} />
}
