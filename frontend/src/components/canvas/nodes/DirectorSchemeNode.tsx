import { useEffect, useMemo, useRef, useState } from 'react'
import { useReactFlow, useStore, type NodeProps } from '@xyflow/react'
import { saveBoard, schemeGenerate } from '../../../api/canvas'
import type { CanvasEdge, CanvasNode, CanvasNodeData } from '../../../lib/canvasTypes'
import { api } from '../../../api/client'
import { cn } from '../../../lib/cn'
import { GEN_RESOLUTIONS } from '../../../lib/videoConfig'

export interface DirectorRow {
  node_id: string
  label: string
  prompt: string
  duration_sec: number
  from_prev: boolean
  /** true = 由画布连线引入（2026-08-30：断线时自动移除该行） */
  _wired?: boolean
}

const BTN = 'rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50'
const SEL = 'rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-700'

export function DirectorSchemeNode({ id, data, selected }: NodeProps<CanvasNode>) {
  const { setNodes } = useReactFlow<CanvasNode>()
  const edges = useStore((s) => s.edges)
  const nodes = useStore((s) => s.nodes)

  const [taskType, setTaskType] = useState(data.taskType ?? (data.schemeKey === 'director_text' ? 't2v' : 'r2v'))
  const [ratio, setRatio] = useState(data.ratio ?? '16:9')
  const [res, setRes] = useState(data.res ?? '0.7mp')
  const [context, setContext] = useState(data.contextEnabled ?? true)
  const [contextFrames, setContextFrames] = useState(data.contextFrames ?? 22)
  const [rows, setRows] = useState<DirectorRow[]>(() =>
    (data.shots ?? []).map((s) => ({
      node_id: s.node_id,
      label: s.label ?? s.node_id.slice(0, 8),
      prompt: s.prompt ?? '',
      duration_sec: s.duration_sec ?? 5,
      from_prev: s.from_prev ?? true,
    })),
  )
  const [submitting, setSubmitting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const patchSelf = (patch: Partial<CanvasNodeData>) => {
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)))
  }

  useEffect(() => {
    const wired = edges
      .filter((e) => {
        const semantic = e.data?.semantic as string | undefined
        return e.target === id && semantic && (semantic === 'continuation' || semantic === 'sequence')
      })
      .map((e) => e.source)
    const wiredSet = new Set(wired)
    // 断线/删除 → 移除由连线引入且已不再连接的行（2026-08-30 双向同步）
    setRows((prev) => prev.filter((r) => !r._wired || wiredSet.has(r.node_id)))
    // 新增连线 → 追加（去重；已存在的 wire 行不重复）
    setRows((prev) => {
      const known = new Set(prev.map((r) => r.node_id))
      const added: DirectorRow[] = []
      for (const sid of wired) {
        if (known.has(sid)) continue
        const n = nodes.find((x) => x.id === sid)
        const nd = (n?.data ?? {}) as Partial<CanvasNodeData>
        added.push({
          node_id: sid,
          label: nd.label ?? sid.slice(0, 8),
          prompt: nd.prompt ?? nd.description ?? '',
          duration_sec: nd.durationSec ?? 5,
          from_prev: prev.length > 0,
          _wired: true,
        })
      }
      return added.length ? [...prev, ...added] : prev
    })
  }, [edges, id, nodes])

  useEffect(() => {
    patchSelf({ taskType, ratio, res, contextEnabled: context, contextFrames, shots: rows.length ? rows : undefined })
  }, [taskType, ratio, res, context, contextFrames, rows])

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const move = (idx: number, dir: -1 | 1) =>
    setRows((prev) => {
      const to = idx + dir
      if (to < 0 || to >= prev.length) return prev
      const next = [...prev]
      const [it] = next.splice(idx, 1)
      next.splice(to, 0, it)
      return next
    })
  const setRow = (nodeId: string, patch: Partial<DirectorRow>) =>
    setRows((prev) => prev.map((r) => (r.node_id === nodeId ? { ...r, ...patch } : r)))
  const addFromCanvas = (nodeId: string) => {
    const n = nodes.find((x) => x.id === nodeId)
    if (!n) return
    const nd = (n.data ?? {}) as Partial<CanvasNodeData>
    setRows((prev) =>
      prev.some((r) => r.node_id === nodeId)
        ? prev
        : [...prev, {
            node_id: nodeId,
            label: nd.label ?? nodeId.slice(0, 8),
            prompt: nd.prompt ?? nd.description ?? '',
            duration_sec: nd.durationSec ?? 5,
            from_prev: prev.length > 0,
            _wired: false,
          }],
    )
  }
  const shotCandidates = useMemo(
    () => nodes.filter((n) => n.type === 'shot' && n.id !== id && !rows.some((r) => r.node_id === n.id)),
    [nodes, rows, id],
  )

  const submit = async () => {
    if (submitting) return
    const boardId = localStorage.getItem('canvas-board-id-v1')
    if (!boardId) { patchSelf({ error: '未联机画布（缺少 board id），请刷新画布页' }); return }
    const valid = rows.filter((r) => r.node_id)
    if (!valid.length) { patchSelf({ error: '方案需要至少 1 个分镜（连线装配或表内添加）' }); return }
    // 2026-08-30:提交前校验已装配分镜是否仍在画布（防删除后带失效 node_id 提交导致整方案失败）
    const saNodeIds = new Set(nodes.map((n) => n.id))
    const missing = valid.filter((r) => !saNodeIds.has(r.node_id))
    if (missing.length) {
      patchSelf({ error: '以下分镜已不在画布: ' + missing.map((m) => m.label).slice(0, 3).join('、') + '；请删除对应行后再执行' })
      return
    }
    const scheme = taskType === 't2v' ? 'director_text' : 'director'
    setSubmitting(true)
    // 提交前先落库当前文档：保证后端已包含本方案节点（否则完成时的 canvas:refresh 会用旧文档把它冲掉）
    try {
      await saveBoard(boardId, { document: { nodes: nodes as CanvasNode[], edges: edges as CanvasEdge[] } })
    } catch { /* 保存失败不阻断提交 */ }
    patchSelf({ status: 'queued', progress: 0, error: undefined, videoError: undefined })
    let stopping = false
    const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
    const tick = async () => {
      if (stopping) return
      try {
        const t = await api.get<{ status: string; progress: number }>('/tasks/' + out.task_id)
        const st = t.status
        if (st === 'succeeded') {
          stopping = true
          patchSelf({ status: 'done', progress: 100 })
          setSubmitting(false)
          stopPoll()
          window.dispatchEvent(new Event('canvas:refresh'))
        } else if (st === 'failed' || st === 'cancelled') {
          stopping = true
          patchSelf({ status: 'failed', error: '任务失败/取消', videoError: '见任务中心' })
          setSubmitting(false)
          stopPoll()
        } else {
          patchSelf({ status: st === 'pending' ? 'queued' : 'running', progress: t.progress })
        }
      } catch { }
    }
    let out: { task_id: string }
    try {
      const submitted = await schemeGenerate(boardId, {
        scheme,
        node_ids: valid.map((r) => r.node_id),
        shots: valid.map((r) => ({
          node_id: r.node_id,
          prompt: r.prompt.trim() ? r.prompt.trim() : undefined,
          duration_sec: r.duration_sec || undefined,
          from_prev: r.from_prev,
        })),
        config: { ratio, res, context_enabled: context, context_frames: contextFrames, scheme_node_id: id },
      })
      out = { task_id: submitted.task_id }
      patchSelf({ directorTaskId: out.task_id, status: 'running', progress: 3 })
      pollRef.current = setInterval(tick, 3000)
      tick()
    } catch (e) {
      patchSelf({ status: 'failed', error: String(e) })
      setSubmitting(false)
    }
  }

  const busy = data.status === 'queued' || data.status === 'running'
  const err = data.error || data.videoError
  const videoUrl = data.videoUrl || data.previewUrl

  return (
    <div
      className={cn(
        'w-[560px] overflow-hidden rounded-xl border bg-white shadow-md transition-shadow',
        selected ? 'border-brand-400 ring-2 ring-brand-200' : 'border-slate-200',
      )}
    >
      <div className="flex items-center justify-between bg-slate-800 px-3 py-1.5">
        <div className="flex items-center gap-1.5">
          <span className="rounded bg-brand-500 px-1 py-px text-[9px] font-bold text-white">方案</span>
          <span className="text-[12px] font-semibold text-slate-100">{data.schemeLabel ?? '多段连拍·整片'}</span>
        </div>
        <div className="flex items-center gap-1.5 text-[9px]">
          <span
            className={cn(
              'rounded-full px-1.5 py-0.5 font-medium',
              data.status === 'done' ? 'bg-emerald-500/20 text-emerald-300'
              : data.status === 'failed' ? 'bg-red-500/20 text-red-300'
              : data.status === 'running' || data.status === 'queued' ? 'bg-amber-500/20 text-amber-300'
              : 'bg-white/10 text-slate-300',
            )}
          >
            {data.status === 'done' ? '✓ 完成' : data.status === 'failed' ? '⛔ 失败' : busy ? '生成中' : data.status === 'queued' ? '排队中' : '待命'}
          </span>
          <span className="rounded bg-white/10 px-1.5 py-0.5 text-slate-300">{taskType === 't2v' ? 't2v' : 'r2v'}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-3 py-2">
        <label className="text-[10px] font-medium text-slate-500">任务类型
          <select value={taskType} onChange={(e) => setTaskType(e.target.value)} className={SEL}>
            <option value="r2v">r2v 参考出片</option>
            <option value="t2v">t2v 纯文生</option>
          </select>
        </label>
        <label className="text-[10px] font-medium text-slate-500">比例
          <select value={ratio} onChange={(e) => setRatio(e.target.value)} className={SEL}>
            {['16:9', '9:16', '4:3', '3:4', '1:1'].map((x) => <option key={x}>{x}</option>)}
          </select>
        </label>
        <label className="text-[10px] font-medium text-slate-500">档位
          <select value={res} onChange={(e) => setRes(e.target.value)} className={SEL}>
            {GEN_RESOLUTIONS.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-[10px] font-medium text-slate-500">
          <input type="checkbox" checked={context} onChange={(e) => setContext(e.target.checked)} className="accent-brand-500" />
          段间引导
        </label>
        {context && (
          <label className="text-[10px] font-medium text-slate-500">上下文
            <select value={contextFrames} onChange={(e) => setContextFrames(Number(e.target.value))} className={SEL}>
              {[5, 22, 39, 56].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        )}
      </div>

      <div className="max-h-[46vh] overflow-y-auto px-3 py-2">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[10px] font-semibold text-slate-500">分段脚本（按序出片 · {rows.length} 镜 · 合计约 {rows.reduce((s, r) => s + (r.duration_sec || 0), 0).toFixed(1)}s）</span>
          {shotCandidates.length > 0 && (
            <select
              defaultValue=""
              onChange={(e) => { if (e.target.value) { addFromCanvas(e.target.value); (e.target as HTMLSelectElement).value = '' } }}
              className={SEL + ' max-w-[180px]'}
            >
              <option value="" disabled>＋ 添加画布分镜…</option>
              {shotCandidates.map((n) => <option key={n.id} value={n.id}>{((n.data ?? {}) as Partial<CanvasNodeData>).label ?? n.id.slice(0, 8)}</option>)}
            </select>
          )}
        </div>
        {rows.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-200 py-4 text-center text-[10px] text-slate-400">
            暂无分镜 —— 用「衔接/承接」线把分镜节点连接到这里，或从上方「添加画布分镜」
          </div>
        )}
        <div className="space-y-1.5">
          {rows.map((r, i) => (
            <div key={r.node_id} className="rounded-lg border border-slate-200 bg-white px-2 py-1.5">
              <div className="flex items-center gap-1.5">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-slate-800 text-[10px] font-bold text-white">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-slate-700" title={r.label}>{r.label}</span>
                <button type="button" disabled={i === 0} onClick={() => move(i, -1)} className={cn(BTN, 'px-1', i === 0 && 'opacity-25')}>▲</button>
                <button type="button" disabled={i === rows.length - 1} onClick={() => move(i, 1)} className={cn(BTN, 'px-1', i === rows.length - 1 && 'opacity-25')}>▼</button>
                <input
                  type="number" min={1} max={15} value={r.duration_sec}
                  onChange={(e) => setRow(r.node_id, { duration_sec: Number(e.target.value) })}
                  className="w-12 rounded-md border border-slate-200 px-1 py-0.5 text-center text-[10px] outline-none focus:border-brand-300"
                />
                <span className="text-[9px] text-slate-400">s</span>
                <label className="flex items-center gap-0.5 text-[9px] text-slate-500">
                  <input type="checkbox" disabled={i === 0} checked={r.from_prev} onChange={(e) => setRow(r.node_id, { from_prev: e.target.checked })} className="accent-brand-500" />
                  接上段
                </label>
                <button type="button" onClick={() => setRows((p) => p.filter((x) => x.node_id !== r.node_id))} className="text-[10px] text-slate-400 hover:text-red-500">✕</button>
              </div>
              <textarea
                value={r.prompt}
                onChange={(e) => setRow(r.node_id, { prompt: e.target.value })}
                rows={2}
                className="mt-1 w-full resize-y rounded-md border border-slate-200 bg-slate-50/60 px-1.5 py-1 text-[10px] leading-snug outline-none focus:border-brand-300"
                placeholder="镜头运动/动作/构图（留空=用分镜描述）"
              />
            </div>
          ))}
        </div>
      </div>

      <div className="border-t border-slate-100 bg-slate-50/60 px-3 py-2">
        {data.status === 'running' || data.status === 'queued' ? (
          <div className="flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
              <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: (data.progress ?? 0) + '%' }} />
            </div>
            <span className="text-[10px] text-slate-500">{data.progress ?? 0}%</span>
          </div>
        ) : (
          <button
            disabled={submitting}
            onClick={submit}
            className="w-full rounded-lg bg-brand-500 py-1.5 text-[11px] font-semibold text-white hover:bg-brand-600 disabled:opacity-40"
          >
            {submitting ? '提交中…' : `▶ 执行方案(${rows.length} 镜 → 整片)`}
          </button>
        )}
        {videoUrl && (
          <video controls src={videoUrl} className="mt-2 w-full rounded-lg border border-slate-200 bg-slate-900" style={{ maxHeight: 180 }} />
        )}
        {err && <p className="mt-1 text-[9px] leading-snug text-red-500">{err}</p>}
        <p className="mt-1 text-[9px] text-slate-400">产物整片回写画布（video 节点 + 分镜标记）。任务进度与回写由后端执行；改动自动保存。</p>
      </div>
    </div>
  )
}
