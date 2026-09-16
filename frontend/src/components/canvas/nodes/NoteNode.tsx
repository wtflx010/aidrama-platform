import type { NodeProps } from '@xyflow/react'
import { cn } from '../../../lib/cn'
import type { CanvasNode } from '../../../lib/canvasTypes'

/** 便签节点 */
export function NoteNode({ data, selected }: NodeProps<CanvasNode>) {
  const d = data
  return (
    <div
      className={cn(
        'w-[220px] rounded-lg border bg-yellow-50 p-2.5 shadow-sm',
        selected ? 'border-amber-300 ring-2 ring-brand-500/70 shadow-md' : 'border-yellow-200',
      )}
    >
      <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-slate-600">{d.noteText ?? '（空白便签）'}</p>
    </div>
  )
}
