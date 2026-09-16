import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../lib/cn'
import { Icon } from '../../lib/icons'

type ToastType = 'success' | 'error' | 'info' | 'warning'

interface Toast {
  id: number
  type: ToastType
  message: string
}

interface ToastContextValue {
  toast: {
    success: (msg: string) => void
    error: (msg: string) => void
    info: (msg: string) => void
    warning: (msg: string) => void
  }
}

const ToastContext = createContext<ToastContextValue | null>(null)

const CONFIG: Record<ToastType, { icon: string; cls: string; iconCls: string }> = {
  success: { icon: 'check', cls: 'border-emerald-500/30', iconCls: 'text-emerald-600 bg-emerald-50' },
  error: { icon: 'alert-circle', cls: 'border-rose-500/30', iconCls: 'text-rose-500 bg-rose-50' },
  info: { icon: 'info', cls: 'border-sky-500/30', iconCls: 'text-sky-600 bg-sky-50' },
  warning: { icon: 'alert-circle', cls: 'border-amber-500/30', iconCls: 'text-amber-500 bg-amber-50' },
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const remove = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const push = useCallback(
    (type: ToastType, message: string) => {
      const id = Date.now() + Math.random()
      setToasts((prev) => [...prev, { id, type, message }])
      setTimeout(() => remove(id), 4000)
    },
    [remove],
  )

  const toast = {
    success: (msg: string) => push('success', msg),
    error: (msg: string) => push('error', msg),
    info: (msg: string) => push('info', msg),
    warning: (msg: string) => push('warning', msg),
  }

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      {createPortal(
        <div className="fixed bottom-6 right-6 z-[100] flex flex-col gap-2 pointer-events-none">
          {toasts.map((t) => {
            const cfg = CONFIG[t.type]
            return (
              <div
                key={t.id}
                className={cn(
                  'pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-xl',
                  'bg-white/95 backdrop-blur-xl border shadow-xl',
                  'animate-slide-in-right min-w-[280px] max-w-[400px]',
                  cfg.cls,
                )}
              >
                <div className={cn('flex items-center justify-center w-7 h-7 rounded-lg shrink-0', cfg.iconCls)}>
                  <Icon name={cfg.icon} size={16} />
                </div>
                <p className="text-sm text-slate-800 flex-1">{t.message}</p>
                <button
                  onClick={() => remove(t.id)}
                  className="text-slate-400 hover:text-slate-600 shrink-0"
                >
                  <Icon name="x" size={14} />
                </button>
              </div>
            )
          })}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

export function useToast(): ToastContextValue['toast'] {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    // 降级：无 Provider 时返回 noop，避免崩溃
    const noop = () => {}
    return { success: noop, error: noop, info: noop, warning: noop }
  }
  return ctx.toast
}
