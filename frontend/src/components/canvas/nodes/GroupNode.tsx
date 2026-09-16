import type { NodeProps } from '@xyflow/react'
import { cn } from '../../../lib/cn'
import type { CanvasNode } from '../../../lib/canvasTypes'

/** 分组框:幕/场/动作序列(M0 为静态容器,不参与连线) */
export function GroupNode({ data, selected }: NodeProps<CanvasNode>) {
  const d = data
  return (
    <div
      className={cn(
        'pointer-events-none rounded-2xl border-2 border-dashed bg-slate-100/40',
        selected ? 'border-brand-500/70' : 'border-slate-300/80',
      )}
      style={{ width: 920, height: 560 }}
    >
      <div className="pointer-events-auto absolute left-3 top-2.5 flex items-center gap-1.5 rounded-md bg-white/90 px-2 py-1 text-[11px] font-semibold text-slate-600 shadow-sm">
        <span className="h-1.5 w-1.5 rounded-full bg-brand-500" />
        {d.label ?? '分组'}
        {typeof d.memberCount === 'number' && <span className="text-slate-400">{d.memberCount} 镜</span>}
      </div>
    </div>
  )
}
