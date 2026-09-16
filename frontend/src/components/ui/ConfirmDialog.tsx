import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'
import { Modal } from './Modal'
import { Button } from './Button'
import { Icon } from '../../lib/icons'

interface ConfirmOptions {
  title?: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>

const ConfirmContext = createContext<ConfirmFn | null>(null)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfirmOptions | null>(null)
  const [resolver, setResolver] = useState<((v: boolean) => void) | null>(null)

  const confirm = useCallback((options: ConfirmOptions) => {
    setState(options)
    return new Promise<boolean>((resolve) => {
      setResolver(() => resolve)
    })
  }, [])

  const close = (result: boolean) => {
    resolver?.(result)
    setState(null)
    setResolver(null)
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        open={!!state}
        onClose={() => close(false)}
        title={state?.title ?? '确认操作'}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => close(false)}>
              {state?.cancelText ?? '取消'}
            </Button>
            <Button
              variant={state?.danger ? 'danger' : 'primary'}
              size="sm"
              onClick={() => close(true)}
            >
              {state?.confirmText ?? '确认'}
            </Button>
          </>
        }
      >
        <div className="flex items-start gap-3 py-2">
          <div
            className={`flex items-center justify-center w-10 h-10 rounded-xl shrink-0 ${
              state?.danger
                ? 'bg-rose-50 text-rose-500'
                : 'bg-amber-50 text-amber-500'
            }`}
          >
            <Icon name="alert-circle" size={20} />
          </div>
          <p className="text-sm text-slate-600 pt-1.5">{state?.message}</p>
        </div>
      </Modal>
    </ConfirmContext.Provider>
  )
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext)
  if (!ctx) {
    // 降级到原生 confirm
    return (options) => Promise.resolve(window.confirm(options.message))
  }
  return ctx
}
