import type { NodeProps } from '@xyflow/react'
import { cn } from '../../../lib/cn'
import type { CanvasNode } from '../../../lib/canvasTypes'
import { NodeHandles } from './NodeHandles'

const KIND_STYLE = {
  character: { label: '角色', cls: 'bg-violet-100 text-violet-700' },
  scene: { label: '场景', cls: 'bg-sky-100 text-sky-700' },
  prop: { label: '道具', cls: 'bg-amber-100 text-amber-700' },
} as const

/** 资产节点:角色/场景/道具参考图(对应 AssetPanel 资产体系) */
export function AssetNode({ data, selected }: NodeProps<CanvasNode>) {
  const d = data
  const kind = d.kind ?? 'character'
  const ks = KIND_STYLE[kind]
  const last = d.versions && d.versions.length > 0 ? d.versions[d.versions.length - 1] : undefined
  const img = last?.url ?? d.imageUrl

  return (
    <div
      className={cn(
        'w-[188px] overflow-hidden rounded-xl border bg-white shadow-sm transition-shadow',
        selected ? 'border-slate-300 ring-2 ring-brand-500/70 shadow-md' : 'border-slate-200 hover:shadow-md',
        d.status === 'failed' && 'border-red-300',
      )}
    >
      <NodeHandles />
      <div className="flex items-center justify-between px-2.5 pt-2 pb-1">
        <span className="text-[11px] font-semibold text-slate-700">{d.label ?? '资产'}</span>
        <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium', ks.cls)}>{ks.label}</span>
      </div>
      <div className="relative mx-2">
        {d.status === 'failed' && (
          <div className="absolute inset-x-0 bottom-0 z-10 rounded-b-lg bg-red-600/85 px-2 py-1 text-[10px] leading-snug text-white">
            {(d.error ?? '生成失败').slice(0, 46)}
          </div>
        )}
        {img ? (
          <img src={img} alt={d.label ?? ''} className="h-[130px] w-full rounded-lg object-cover" />
        ) : (
          <div className="grid h-[130px] w-full place-items-center rounded-lg bg-slate-100 text-[11px] text-slate-400">
            未生成资产图
          </div>
        )}
      </div>
      {d.description && (
        <p className="px-2.5 py-1.5 text-[10px] leading-relaxed text-slate-400 line-clamp-2">{d.description}</p>
      )}
    </div>
  )
}
