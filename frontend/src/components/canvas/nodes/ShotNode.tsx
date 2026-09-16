import { useNavigate } from 'react-router-dom'
import type { NodeProps } from '@xyflow/react'
import { useCanvasAction } from '../canvasContext'
import { cn } from '../../../lib/cn'
import { COMPOSITION_POINTS, type CanvasNode } from '../../../lib/canvasTypes'
import { NodeHandles } from './NodeHandles'

/** 分镜节点:关键帧缩略图 + 九宫格点位角标 + 版本计数 + 快捷生成 */
export function ShotNode({ id, data, selected }: NodeProps<CanvasNode>) {
  const act = useCanvasAction()
  const navigate = useNavigate()
  const d = data
  const last = d.versions && d.versions.length > 0 ? d.versions[d.versions.length - 1] : undefined
  const img = last?.url ?? d.imageUrl
  const busy = d.status === 'running' || d.status === 'queued'
  const failed = d.status === 'failed'
  const cp = d.compositionPoint ? COMPOSITION_POINTS[d.compositionPoint] : undefined

  return (
    <div
      className={cn(
        'w-[236px] overflow-hidden rounded-xl border bg-white shadow-sm transition-shadow',
        selected ? 'border-slate-300 ring-2 ring-brand-500/70 shadow-md' : 'border-slate-200 hover:shadow-md',
        failed && 'border-red-300',
      )}
    >
      <NodeHandles />
      {/* 头部 */}
      <div className="flex items-center justify-between px-2.5 pt-2 pb-1">
        <span className="text-[11px] font-semibold text-slate-700">{d.label ?? '分镜'}</span>
        {failed ? (
          <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-600">失败</span>
        ) : busy ? (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
            {d.status === 'queued' ? '排队' : '生成中'}
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] text-slate-400">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" />
            {d.versions?.length ?? 0} 版本
          </span>
        )}
      </div>
      {/* 关键帧图 */}
      <div className="relative mx-2 rounded-lg">
        {img ? (
          <img src={img} alt={d.label ?? ''} className="h-[128px] w-full rounded-lg object-cover" />
        ) : (
          <div className="grid h-[128px] w-full place-items-center rounded-lg bg-slate-100 text-[11px] text-slate-400">
            未生成关键帧
          </div>
        )}
        {/* 九宫格点位角标 */}
        {cp && (
          <span className="absolute bottom-1.5 left-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">
            ◉ {cp}
          </span>
        )}
        {failed && d.error && (
          <div className="absolute inset-x-0 bottom-0 rounded-b-lg bg-red-600/85 px-2 py-1 text-[10px] leading-snug text-white">
            {(d.error ?? '').slice(0, 46)}
          </div>
        )}
        {busy && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-lg bg-slate-900/60 text-[11px] text-white">
            <span>{d.status === 'queued' ? '排队中…' : `生成中 ${d.progress ?? 0}%`}</span>
            <div className="h-1 w-3/4 overflow-hidden rounded bg-white/30">
              <div
                className="h-full rounded bg-emerald-400 transition-all duration-300"
                style={{ width: `${d.progress ?? 0}%` }}
              />
            </div>
          </div>
        )}
      </div>
      {/* 摘要 + 快捷动作 */}
      <div className="flex items-center justify-between px-2.5 py-2">
        <span className="text-[10px] text-slate-400">
          {d.cameraParams ? `${d.cameraParams.speed}·${d.cameraParams.angle}·${d.cameraParams.intensity}` : '未设运镜'}
        </span>
        <span className="flex gap-1">
          {d.segmentId && d.projectId && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                navigate(`/projects/${d.projectId}?shot=${d.segmentId}`)
              }}
              className="rounded-md border border-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50"
              title="在分镜编辑器中打开"
            >
              ✎
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation()
              act({ nodeId: id, action: 'generate-image' })
            }}
            className="rounded-md border border-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50"
          >
            变体
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              act({ nodeId: id, action: 'generate-video' })
            }}
            className="rounded-md bg-brand-500 px-2 py-0.5 text-[10px] font-medium text-white hover:bg-brand-600"
          >
            视频
          </button>
        </span>
      </div>
    </div>
  )
}
