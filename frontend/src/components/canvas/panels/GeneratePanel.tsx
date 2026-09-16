import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { CanvasNode } from '../../../lib/canvasTypes'
import type { Model } from '../../../api/types'
import { api } from '../../../api/client'
import { VersionStack } from '../common/VersionStack'

interface Props {
  node: CanvasNode
  onGenerate: (nodeId: string, kind: 'image' | 'video', modelId?: string) => void
  onSelectVersion: (nodeId: string, index: number) => void
  onExpand?: (nodeId: string) => void
}

const RATIOS = ['16:9', '9:16', '1:1'] as const

/** 生成面板(右侧):从模型配置中心拉取真实模型 → 选模型/比例 → 手动触发生成 → 版本栈管理 */
export function GeneratePanel({ node, onGenerate, onSelectVersion, onExpand }: Props) {
  const d = node.data
  const [ratio, setRatio] = useState<string>('16:9')
  const [size, setSize] = useState<string>('2K')

  const { data: models = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['canvas-models'],
    queryFn: () => api.get<Model[]>('/admin/models'),
  })

  const isImageKind = node.type === 'shot' || node.type === 'asset'
  const isVideoKind = node.type === 'shot'

  // 按当前节点类型过滤可用的启用模型（image=生图, video=生视频）
  const available = useMemo(() => {
    if (!isImageKind && !isVideoKind) return []
    return models.filter((m) => m.is_enabled && (isImageKind ? m.model_type === 'image' : m.model_type === 'video'))
  }, [models, isImageKind, isVideoKind])

  const [modelId, setModelId] = useState<string>('')
  // 节点切换 或 模型列表就绪后，把选中项对齐到首个可用模型
  useEffect(() => {
    setModelId(available[0]?.id ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id, available[0]?.id])

  // 把内部 model_key 映射为 /admin/models 的真实 id（兜底：传空串由后端路由默认）
  const resolvedModelId = modelId || (available.find((m) => m.is_default)?.id ?? '')

  if (!isImageKind && !isVideoKind) return null

  const busy = d.status === 'running' || d.status === 'queued'
  const versions = d.versions ?? []
  const active = Math.min(d.activeVersion ?? versions.length - 1, Math.max(versions.length - 1, 0))

  return (
    <section className="border-t border-slate-200 px-3 py-3">
      <h3 className="mb-2 text-[11px] font-semibold text-slate-500">生成面板</h3>

      <label className="block text-[10px] font-medium text-slate-400">模型(模型配置中心)</label>
      {isLoading ? (
        <div className="mt-1 flex h-8 items-center rounded-lg bg-slate-50 px-2 text-[10px] text-slate-400">加载模型…</div>
      ) : isError ? (
        <div className="mt-1 flex h-8 items-center justify-between rounded-lg border border-rose-200 bg-rose-50 px-2 text-[10px] text-rose-500">
          <span>模型加载失败</span>
          <button type="button" onClick={() => refetch()} className="underline">重试</button>
        </div>
      ) : available.length === 0 ? (
        <div className="mt-1 flex h-8 items-center rounded-lg bg-slate-50 px-2 text-[10px] text-slate-400">
          未配置可用的{isImageKind ? '生图' : '生视频'}模型
        </div>
      ) : (
        <select
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          className="mt-0.5 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[11px] text-slate-700"
        >
          {available.map((m) => (
            <option key={m.id} value={m.id}>{m.name || m.model_id}</option>
          ))}
        </select>
      )}

      <div className="mt-2 grid grid-cols-2 gap-2">
        <div>
          <label className="block text-[10px] font-medium text-slate-400">比例</label>
          <select value={ratio} onChange={(e) => setRatio(e.target.value)} className="mt-0.5 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[11px] text-slate-700">
            {RATIOS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] font-medium text-slate-400">尺寸</label>
          <select value={size} onChange={(e) => setSize(e.target.value)} className="mt-0.5 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[11px] text-slate-700">
            <option value="2K">2K</option>
            <option value="1K">1K</option>
          </select>
        </div>
      </div>

      {d.prompt && (
        <div className="mt-2 rounded-lg bg-slate-50 p-2">
          <p className="line-clamp-3 text-[10px] leading-relaxed text-slate-500">{d.prompt}</p>
        </div>
      )}

      <div className="mt-2 flex gap-2">
        {isImageKind && (
          <button disabled={busy} onClick={() => onGenerate(node.id, 'image', resolvedModelId)} className="flex-1 rounded-lg bg-brand-500 px-2 py-1.5 text-[11px] font-semibold text-white hover:bg-brand-600 disabled:opacity-50">
            {busy && d.status === 'queued' ? '排队中…' : busy ? ('生成中 ' + (d.progress ?? 0) + '%') : '开始生成(生图)'}
          </button>
        )}
        {isVideoKind && (
          <button disabled={busy} onClick={() => onGenerate(node.id, 'video', resolvedModelId)} className="flex-1 rounded-lg bg-slate-800 px-2 py-1.5 text-[11px] font-semibold text-white hover:bg-slate-900 disabled:opacity-50">
            {busy && d.status === 'queued' ? '排队中…' : busy ? ('生成中 ' + (d.progress ?? 0) + '%') : '生成视频(R2V)'}
          </button>
        )}
      </div>

      {isImageKind && onExpand && (
        <button type="button" disabled={busy} onClick={() => onExpand(node.id)} className="mt-1.5 w-full rounded-lg border border-slate-200 px-2 py-1 text-[10px] font-medium text-slate-500 hover:bg-slate-50 disabled:opacity-50">
          ✨ AI 扩写提示词(预览,不提交生成)
        </button>
      )}

      {isImageKind && available.length === 0 && (
        <p className="mt-1.5 text-[10px] text-slate-400">请在「模型配置中心」启用一个生图模型后再生成。</p>
      )}

      <p className="mt-1.5 text-[10px] text-slate-400">模型来自系统配置中心;点击生成将提交到后端任务。</p>

      <div className="mt-2">
        <label className="block pb-1 text-[10px] font-medium text-slate-400">版本栈({versions.length})</label>
        <VersionStack versions={versions} active={active} onSelect={(i) => onSelectVersion(node.id, i)} />
      </div>
    </section>
  )
}