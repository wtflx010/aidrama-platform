import { useMemo, useState } from 'react'
import { schemeGenerate, type DirectorGenerateBody, type SchemeGenerateOut } from '../../../api/canvas'
import { cn } from '../../../lib/cn'
import type { CanvasNode } from '../../../lib/canvasTypes'
import { GEN_RESOLUTIONS, type GenResolution } from '../../../lib/videoConfig'

interface DirectorRow {
  node_id: string
  label: string
  prompt: string
  duration_sec: number
  from_prev: boolean
}

interface Props {
  boardId: string | null
  /** 初始分镜节点（按时间顺序,已去重过滤真实分镜） */
  initial: CanvasNode[]
  onClose: () => void
  onSubmitted: (res: SchemeGenerateOut) => void
  onError: (msg: string) => void
  /** 点击表格行 → 在画布上选中并高亮对应节点（画布联动） */
  onSelectShot?: (nodeId: string) => void
  /** 当前画布选中节点 id（行高亮） */
  activeShotId?: string
  /** 方案 key（默认 director）；纯文生组合传 director_text */
  scheme?: string
  /** 方案展示名 */
  schemeLabel?: string
}

const RATIOS = ['16:9', '9:16', '4:3', '3:4', '1:1'] as const
const RESES: readonly GenResolution[] = GEN_RESOLUTIONS

/** 画布导演台模式：多段连续生视频（2026-08-29）。
 *  表格一行=一段镜（顺序=时间顺序）；全局配置走 r2v 公共参数 + 段间引导。
 *  提交后端组装 MiniMaxH3Director timeline_data,产物整片回写画布。
 */
export function DirectorPanel({ boardId, initial, onClose, onSubmitted, onError, scheme = 'director', schemeLabel, onSelectShot, activeShotId }: Props) {
  const [rows, setRows] = useState<DirectorRow[]>(() =>
    initial.map((n) => ({
      node_id: n.id,
      label: n.data.label ?? '分镜',
      prompt: (n.data.prompt ?? n.data.description ?? '').slice(0, 300),
      duration_sec: n.data.durationSec ?? 5,
      from_prev: true,
    })),
  )
  const [ratio, setRatio] = useState<'16:9' | '9:16' | '4:3' | '3:4' | '1:1'>('16:9')
  const [res, setRes] = useState<GenResolution>('0.7mp')
  const [context, setContext] = useState(true)
  const [contextFrames, setContextFrames] = useState(22)
  const [globalPrompt, setGlobalPrompt] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const totalSec = useMemo(() => rows.reduce((s, r) => s + (r.duration_sec || 0), 0), [rows])

  const setRow = (id: string, patch: Partial<DirectorRow>) =>
    setRows((prev) => prev.map((r) => (r.node_id === id ? { ...r, ...patch } : r)))
  const move = (idx: number, dir: -1 | 1) => {
    setRows((prev) => {
      const to = idx + dir
      if (to < 0 || to >= prev.length) return prev
      const next = [...prev]
      const [it] = next.splice(idx, 1)
      next.splice(to, 0, it)
      return next
    })
  }
  const remove = (idx: number) => setRows((prev) => prev.filter((_, i) => i !== idx))

  const submit = async () => {
    if (!boardId) {
      onError('未联机画布,请先刷新进入画布')
      return
    }
    const valid = rows.filter((r) => r.node_id)
    if (!valid.length) {
      onError('导演台需要至少一个分镜节点')
      return
    }
    setSubmitting(true)
    try {
      const body: DirectorGenerateBody = {
        node_ids: valid.map((r) => r.node_id),
        shots: valid.map((r) => ({
          node_id: r.node_id,
          prompt: r.prompt.trim() ? r.prompt.trim() : undefined,
          duration_sec: r.duration_sec || undefined,
          from_prev: r.from_prev,
        })),
        config: {
          ratio, res, context_enabled: context, context_frames: contextFrames,
          global_prompt: globalPrompt.trim() ? globalPrompt.trim() : undefined,
        },
      }
      const out = await schemeGenerate(boardId, { ...body, scheme })
      onSubmitted(out)
    } catch (e) {
      onError(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex h-full w-full flex-col rounded-xl border border-slate-200 bg-white/95 p-3 shadow-xl backdrop-blur">
      <div className="flex h-full w-full flex-col">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-[13px] font-semibold text-slate-700">🎬 {schemeLabel ?? '多段连拍·整片'} · 连续生视频</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="关闭">✕</button>
        </div>
        <p className="pb-2 text-[10px] leading-relaxed text-slate-400">
          选中一组分镜 → 系统组装成一段连续影片批量生成（含段间引导面板衔接）。{scheme === 'director_text' ? '当前为纯文生组合（t2v，不收集资产参考图）。' : ''}
          产量整片将回写画布为「多段连续片」视频节点。166 插件未安装时走 mock 占位验证全链路。
        </p>

        {/* 全局参数 */}
        <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/70 px-2.5 py-2">
          <label className="text-[10px] font-medium text-slate-400">比例
            <select value={ratio} onChange={(e) => setRatio(e.target.value as typeof ratio)} className="ml-1 rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-700">
              {RATIOS.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
          <label className="text-[10px] font-medium text-slate-400">档位
            <select value={res} onChange={(e) => setRes(e.target.value as typeof res)} className="ml-1 rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-700">
              {RESES.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1 text-[10px] font-medium text-slate-500">
            <input type="checkbox" checked={context} onChange={(e) => setContext(e.target.checked)} className="accent-brand-500" />
            段间引导
          </label>
          {context && (
            <label className="text-[10px] font-medium text-slate-400">上下文(帧)
              <select value={contextFrames} onChange={(e) => setContextFrames(Number(e.target.value))} className="ml-1 rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-700">
                {[5, 22, 39, 56].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          )}
          <input
            value={globalPrompt}
            onChange={(e) => setGlobalPrompt(e.target.value)}
            placeholder="全局公共提示词(角色锁定/主题,可留空)"
            className="ml-auto w-[300px] rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] outline-none focus:border-brand-300"
          />
        </div>

        {/* 分段脚本表格 */}
        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-slate-200">
          <table className="w-full border-collapse text-[11px]">
            <thead className="sticky top-0 z-10 bg-slate-50 text-left text-[10px] font-semibold text-slate-500">
              <tr>
                <th className="w-10 border-b border-slate-200 px-1 py-1 text-center">顺序</th>
                <th className="w-8 border-b border-slate-200 px-1 py-1"></th>
                <th className="w-44 border-b border-slate-200 px-1 py-1">镜头</th>
                <th className="border-b border-slate-200 px-1 py-1">提示词（一段=一个镜头的运动/镜头语言）</th>
                <th className="w-20 border-b border-slate-200 px-1 py-1 text-center">时长(s)</th>
                <th className="w-16 border-b border-slate-200 px-1 py-1 text-center">接上段</th>
                <th className="w-10 border-b border-slate-200 px-1 py-1 text-center">删</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={r.node_id}
                  onMouseEnter={() => onSelectShot?.(r.node_id)}
                  onClick={() => onSelectShot?.(r.node_id)}
                  className={
                    'cursor-pointer border-b border-slate-100 ' +
                    (activeShotId === r.node_id ? 'bg-brand-50/60' : 'hover:bg-slate-50/60')
                  }
                >
                  <td className="px-1 py-1 text-center text-slate-400">{i + 1}</td>
                  <td className="px-1 py-1">
                    <div className="flex flex-col gap-0.5">
                      <button type="button" disabled={i === 0} onClick={() => move(i, -1)} className={cn('text-[12px] leading-none text-slate-400', i === 0 ? 'opacity-25' : 'hover:text-slate-700')}>▲</button>
                      <button type="button" disabled={i === rows.length - 1} onClick={() => move(i, 1)} className={cn('text-[12px] leading-none text-slate-400', i === rows.length - 1 ? 'opacity-25' : 'hover:text-slate-700')}>▼</button>
                    </div>
                  </td>
                  <td className="px-1 py-1 align-top">
                    <div className="truncate font-medium text-slate-700">{r.label}</div>
                    <div className="text-[9px] text-slate-400">{r.node_id.slice(0, 8)}</div>
                  </td>
                  <td className="px-1 py-1">
                    <textarea
                      value={r.prompt}
                      onChange={(e) => setRow(r.node_id, { prompt: e.target.value })}
                      rows={2}
                      className="w-full resize-y rounded-md border border-slate-200 bg-white px-1.5 py-1 text-[11px] leading-snug outline-none focus:border-brand-300"
                      placeholder="镜头运动/动作/构图(可留空→用分镜描述)"
                    />
                  </td>
                  <td className="px-1 py-1 text-center">
                    <input
                      type="number"
                      min={1}
                      max={15}
                      value={r.duration_sec}
                      onChange={(e) => setRow(r.node_id, { duration_sec: Number(e.target.value) }) }
                      className="w-14 rounded-md border border-slate-200 bg-white px-1 py-1 text-center text-[11px] outline-none focus:border-brand-300"
                    />
                  </td>
                  <td className="px-1 py-1 text-center">
                    <input
                      type="checkbox"
                      disabled={i === 0}
                      checked={r.from_prev}
                      onChange={(e) => setRow(r.node_id, { from_prev: e.target.checked })}
                      className="accent-brand-500"
                    />
                  </td>
                  <td className="px-1 py-1 text-center">
                    <button type="button" onClick={() => remove(i)} className="text-[10px] text-slate-400 hover:text-red-500">✕</button>
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={7} className="px-2 py-6 text-center text-slate-400">无可用分镜节点(需绑定真实分镜)</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between pt-2">
          <span className="text-[10px] text-slate-400">{rows.length} 段 · 合计约 {totalSec.toFixed(1)}s · {scheme === 'director_text' ? 't2v 纯文生' : 'r2v 参考'} · ref2va</span>
          <div className="flex gap-2">
            <button onClick={onClose} className="rounded-lg border border-slate-200 px-3 py-1.5 text-[11px] text-slate-500 hover:bg-slate-50">取消</button>
            <button
              disabled={submitting || !rows.length}
              onClick={submit}
              className="rounded-lg bg-brand-500 px-4 py-1.5 text-[11px] font-semibold text-white hover:bg-brand-600 disabled:opacity-40"
            >
              {submitting ? '提交中…' : `提交导演台生成(${rows.length} 段)`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
