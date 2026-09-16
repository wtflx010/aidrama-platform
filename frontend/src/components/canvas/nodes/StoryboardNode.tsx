import type { NodeProps } from '@xyflow/react'
import { useCanvasAction } from '../canvasContext'
import { cn } from '../../../lib/cn'
import type { CanvasNode } from '../../../lib/canvasTypes'
import { NodeHandles } from './NodeHandles'

/** 节拍卡节点:剧本动作节拍文字,可扩写为 H3 提示词 */
export function StoryboardNode({ id, data, selected }: NodeProps<CanvasNode>) {
  const act = useCanvasAction()
  const d = data
  return (
    <div
      className={cn(
        'w-[236px] rounded-xl border bg-amber-50/90 shadow-sm transition-shadow',
        selected ? 'border-amber-300 ring-2 ring-brand-500/70 shadow-md' : 'border-amber-200 hover:shadow-md',
      )}
    >
      <NodeHandles />
      <div className="flex items-center justify-between px-2.5 pt-2 pb-1">
        <span className="text-[11px] font-semibold text-amber-800">{d.label ?? '节拍卡'}</span>
        <button
          onClick={(e) => {
            e.stopPropagation()
            act({ nodeId: id, action: 'expand-h3' })
          }}
          className="rounded-md border border-amber-300 bg-white px-2 py-0.5 text-[10px] font-medium text-amber-700 hover:bg-amber-100/60"
        >
          扩写 H3
        </button>
      </div>
      <p className="px-2.5 pb-2 text-[11px] leading-relaxed text-slate-600">{d.description ?? '（空节拍描述）'}</p>
    </div>
  )
}
