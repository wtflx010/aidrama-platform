import { forwardRef, useEffect, useImperativeHandle, useState } from 'react'
import type { CanvasNode, CameraParams, CanvasNodeData } from '../../../lib/canvasTypes'
import { NineGridPicker } from '../common/NineGridPicker'

export interface NodeDetailsHandle {
  save: () => void
}

interface Props {
  node: CanvasNode
  onPatch: (id: string, patch: Partial<CanvasNodeData>) => void
}

type Draft = Partial<CanvasNodeData>

const KIND_LABEL = { character: '角色', scene: '场景', prop: '道具' } as const
const SPEED = ['slow', 'medium', 'fast'] as const
const ANGLE = ['front', 'side', 'low', 'high', 'top'] as const
const INTENSITY = ['subtle', 'normal', 'strong'] as const

/** 节点详情面板:按节点类型编辑业务字段(保存按钮在底部操作栏,经 ref 触发) */
export const NodeDetailsPanel = forwardRef<NodeDetailsHandle, Props>(function NodeDetailsPanel(
  { node, onPatch }: Props,
  ref,
) {
  const d = node.data
  const [draft, setDraft] = useState<Draft>({})

  useEffect(() => {
    setDraft({
      label: d.label,
      description: d.description,
      prompt: d.prompt,
      noteText: d.noteText,
      kind: d.kind,
      compositionPoint: d.compositionPoint,
      cameraParams: d.cameraParams,
      lightingContinuity: d.lightingContinuity,
    })
  }, [node.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch: Draft) => setDraft((p) => ({ ...p, ...patch }))
  const save = () => {
    onPatch(node.id, draft)
  }
  useImperativeHandle(ref, () => ({ save }))
  const cam = (v: Partial<CameraParams>): CameraParams => ({
    speed: v.speed ?? 'medium',
    angle: v.angle ?? 'front',
    intensity: v.intensity ?? 'normal',
  })
  const cp: Partial<CameraParams> = draft.cameraParams ?? {}

  return (
    <section className="space-y-3 px-3 py-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] font-semibold text-slate-500">节点详情</h3>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-400">
          {node.type}
        </span>
      </div>

      {d.error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-2 py-1.5 text-[10px] leading-snug text-red-600">
          <span className="font-semibold">关键帧失败:</span> {d.error}
        </div>
      )}
      {d.videoError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-2 py-1.5 text-[10px] leading-snug text-red-600">
          <span className="font-semibold">视频失败:</span> {d.videoError}
        </div>
      )}
      {d.videoUrl && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-[10px] leading-snug text-slate-500">
          <span className="font-semibold">视频产物:</span>
          <a href={d.videoUrl} target="_blank" rel="noreferrer" className="ml-1 truncate text-brand-600 hover:underline">
            打开播放
          </a>
          <span className="ml-1 text-slate-400">({d.durationSec ?? 5}s)</span>
        </div>
      )}

      <div>
        <label className="block text-[10px] font-medium text-slate-400">名称</label>
        <input
          value={draft.label ?? ''}
          onChange={(e) => set({ label: e.target.value })}
          className="mt-0.5 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-[11px]"
        />
      </div>

      {node.type === 'asset' && (
        <div>
          <label className="block text-[10px] font-medium text-slate-400">类别</label>
          <div className="mt-0.5 grid grid-cols-3 gap-1">
            {(Object.keys(KIND_LABEL) as Array<keyof typeof KIND_LABEL>).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => set({ kind: k })}
                className={
                  'rounded-lg border py-1 text-[10px] ' +
                  (draft.kind === k
                    ? 'border-brand-500 bg-brand-500/10 font-semibold text-brand-600'
                    : 'border-slate-200 text-slate-500 hover:border-slate-300')
                }
              >
                {KIND_LABEL[k]}
              </button>
            ))}
          </div>
        </div>
      )}

      {node.type === 'shot' && (
        <>
          <div>
            <label className="block pb-1 text-[10px] font-medium text-slate-400">九宫格构图点位(P5)</label>
            <NineGridPicker
              value={draft.compositionPoint}
              onChange={(v) => set({ compositionPoint: v })}
            />
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <div>
              <label className="block text-[10px] font-medium text-slate-400">速度</label>
              <select
                value={cp.speed ?? 'medium'}
                onChange={(e) => set({ cameraParams: cam({ ...cp, speed: e.target.value as CameraParams['speed'] }) })}
                className="mt-0.5 w-full rounded-lg border border-slate-200 px-1.5 py-1.5 text-[10px]"
              >
                {SPEED.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-medium text-slate-400">角度</label>
              <select
                value={cp.angle ?? 'front'}
                onChange={(e) => set({ cameraParams: cam({ ...cp, angle: e.target.value as CameraParams['angle'] }) })}
                className="mt-0.5 w-full rounded-lg border border-slate-200 px-1.5 py-1.5 text-[10px]"
              >
                {ANGLE.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-medium text-slate-400">幅度</label>
              <select
                value={cp.intensity ?? 'normal'}
                onChange={(e) => set({ cameraParams: cam({ ...cp, intensity: e.target.value as CameraParams['intensity'] }) })}
                className="mt-0.5 w-full rounded-lg border border-slate-200 px-1.5 py-1.5 text-[10px]"
              >
                {INTENSITY.map((i) => <option key={i} value={i}>{i}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-[10px] font-medium text-slate-400">光色接续</label>
            <textarea
              value={draft.lightingContinuity ?? ''}
              onChange={(e) => set({ lightingContinuity: e.target.value })}
              rows={2}
              className="mt-0.5 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-[10px]"
            />
          </div>
        </>
      )}

      {(node.type === 'shot' || node.type === 'asset') && (
        <div>
          <label className="block text-[10px] font-medium text-slate-400">提示词</label>
          <textarea
            value={draft.prompt ?? ''}
            onChange={(e) => set({ prompt: e.target.value })}
            rows={4}
            className="mt-0.5 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-[10px] leading-relaxed"
          />
        </div>
      )}

      {node.type === 'storyboard' && (
        <div>
          <label className="block text-[10px] font-medium text-slate-400">节拍描述</label>
          <textarea
            value={draft.description ?? ''}
            onChange={(e) => set({ description: e.target.value })}
            rows={4}
            className="mt-0.5 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-[10px] leading-relaxed"
          />
        </div>
      )}

      {node.type === 'note' && (
        <div>
          <label className="block text-[10px] font-medium text-slate-400">便签内容</label>
          <textarea
            value={draft.noteText ?? ''}
            onChange={(e) => set({ noteText: e.target.value })}
            rows={4}
            className="mt-0.5 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-[10px] leading-relaxed"
          />
        </div>
      )}

      {node.type === 'video' && (
        <p className="text-[10px] text-slate-400">
          视频节点为生成产物;时长 {d.durationSec ?? 0}s。M0 仅模拟播放,不落库。
        </p>
      )}
    </section>
  )
})
