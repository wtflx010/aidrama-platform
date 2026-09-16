import {useState, useEffect, useRef} from 'react'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import type {Model} from '../../api/types'


export function ModelSelector({
  models,
  value,
  onChange,
  disabled,
}: {
  models: Model[]
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const current = models.find((m) => m.id === value)

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex items-center gap-1.5 text-xs font-medium rounded-lg px-2 py-1 transition-colors',
          'text-slate-600 hover:bg-slate-100',
          open && 'bg-slate-100 text-brand-600',
          disabled && 'opacity-50 cursor-not-allowed',
        )}
      >
        <Icon name="settings" size={13} />
        <span className="max-w-[140px] truncate">{current ? current.name : '自动选择'}</span>
        <Icon name="chevron-down" size={12} className="opacity-60" />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 mb-1.5 w-60 rounded-xl border border-slate-200 bg-white shadow-lg py-1 z-30">
          <button
            type="button"
            onClick={() => {
              onChange('')
              setOpen(false)
            }}
            className={cn(
              'w-full flex items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-slate-50',
              !value ? 'text-brand-600 font-medium' : 'text-slate-600',
            )}
          >
            <Icon name="loader-2" size={13} className="opacity-50" />
            自动选择（推荐）
            {!value && <Icon name="check" size={13} className="ml-auto text-brand-500" />}
          </button>
          <div className="my-1 border-t border-slate-100" />
          {models.length === 0 && <p className="px-3 py-1.5 text-xs text-slate-400">暂无可用文本模型</p>}
          {models.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => {
                onChange(m.id)
                setOpen(false)
              }}
              className={cn(
                'w-full flex items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-slate-50',
                m.id === value ? 'text-brand-600 font-medium' : 'text-slate-600',
              )}
            >
              <Icon name="loader-2" size={13} className="opacity-50" />
              <span className="flex-1 truncate">{m.name}</span>
              {m.id === value && <Icon name="check" size={13} className="text-brand-500" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 消息气泡 ─────────────────────────────────────────

