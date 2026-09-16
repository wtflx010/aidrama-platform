/**
 * 资产详情页：单个资产的完整素材展示与再生成。
 * - 角色：封面（文生图）+ 四视图（封面驱动的图生图单张四格 / 旧链路四张分图）
 * - 场景：封面（文生图）+ 多视角（封面驱动的 3x2 六格 POV 图）
 * - 道具：封面
 * 每个素材可单独重新生成（复用 generate-cover / generate-fourview / generate-scene-multiview）。
 */
import { useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useTaskPoller } from '../api/useTaskPoller'
import type { ArtStyle, Asset, AssetGenerateResp } from '../api/types'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { EmptyState } from '../components/ui/EmptyState'
import { ProgressBar } from '../components/ui/ProgressBar'
import { useToast } from '../components/ui/Toast'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

const TYPE_LABEL: Record<string, { text: string; cls: string }> = {
  character: { text: '角色', cls: 'bg-brand-500/10 text-brand-600 border-brand-200' },
  scene: { text: '场景', cls: 'bg-emerald-500/10 text-emerald-600 border-emerald-200' },
  prop: { text: '道具', cls: 'bg-orange-500/10 text-orange-600 border-orange-200' },
}

const STATUS_VARIANT: Record<string, 'gray' | 'blue' | 'green' | 'red'> = {
  pending: 'gray',
  running: 'blue',
  succeeded: 'green',
  failed: 'red',
}

export default function AssetDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const [taskId, setTaskId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [showPrompt, setShowPrompt] = useState(false)

  const { data: asset } = useQuery({
    queryKey: ['asset', id],
    queryFn: () => api.get<Asset>('/assets/' + id),
  })

  const task = useTaskPoller(taskId)
  const busy = !!task && (task.status === 'pending' || task.status === 'running')

  // 方案A：风格指纹 → 展示生成风格
  const { data: styles = [] } = useQuery({
    queryKey: ['art-styles'],
    queryFn: () => api.get<ArtStyle[]>('/art-styles'),
  })

  useEffect(() => {
    if (!task) return
    if (task.status === 'succeeded' || task.status === 'failed' || task.status === 'cancelled') {
      setTaskId(null)
      qc.invalidateQueries({ queryKey: ['asset', id] })
      qc.invalidateQueries({ queryKey: ['asset-library'] })
      if (task.status === 'succeeded') toast.success('生成完成')
      else if (task.status === 'failed') toast.error((task as { error?: string }).error || '生成失败')
    }
  }, [task, id, qc, toast])

  async function handleDelete() {
    if (!asset || deleting) return
    if (!window.confirm('删除资产「' + asset.name + '」？封面/四视图/多视角等文件将一并删除，不可恢复。')) return
    setDeleting(true)
    try {
      await api.del('/assets/' + asset.id)
      qc.invalidateQueries({ queryKey: ['asset-library'] })
      toast.success('资产已删除')
      navigate('/assets')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  function regenerate(kind: 'cover' | 'fourview' | 'multiview') {
    if (busy || !asset) return
    const ep =
      kind === 'cover' ? 'generate-cover'
        : kind === 'fourview' ? 'generate-fourview'
          : 'generate-scene-multiview'
    setTaskId(null)
    api
      .post<AssetGenerateResp>('/assets/' + asset.id + '/' + ep)
      .then((r) => {
        setTaskId(r.task.id)
        qc.invalidateQueries({ queryKey: ['asset', id] })
      })
      .catch((e: Error) => toast.error(e.message))
  }

  if (!asset) {
    return (
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-12">
        <EmptyState icon="layers" title="资产不存在或已删除" description="返回美术查看其他资产" />
      </div>
    )
  }

  const tl = TYPE_LABEL[asset.type] ?? { text: asset.type, cls: 'bg-slate-500/10 text-slate-500 border-slate-200' }
  const isCharacter = asset.type === 'character'
  const isScene = asset.type === 'scene'
  const st = STATUS_VARIANT[asset.status] ?? 'gray'
  const stText = asset.status === 'succeeded' ? '已完成' : asset.status === 'running' ? '生成中' : asset.status === 'failed' ? '失败' : asset.status === 'pending' ? '排队中' : asset.status

  const extraImgs: { url: string; label: string }[] = []
  if (Array.isArray(asset.art_versions)) {
    asset.art_versions.filter(Boolean).forEach((x, i) => {
      const url = typeof x === 'string' ? x : (x as { image_url?: string }).image_url
      if (url) extraImgs.push({ url, label: '风格版本 ' + (i + 1) })
    })
  }
  if (Array.isArray(asset.reference_images)) {
    asset.reference_images.filter(Boolean).forEach((x, i) => {
      const url = typeof x === 'string' ? x : (x as { image_url?: string }).image_url
      if (url) extraImgs.push({ url, label: '参考图 ' + (i + 1) })
    })
  }

  function GenButton({ kind, label, icon }: { kind: 'cover' | 'fourview' | 'multiview'; label: string; icon: string }) {
    return (
      <Button
        size="sm"
        variant="outline"
        leftIcon={<Icon name={icon} size={13} />}
        loading={busy}
        disabled={busy}
        onClick={() => regenerate(kind)}
      >
        {label}
      </Button>
    )
  }

  return (
    <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 animate-fade-in">
      {/* 面包屑 */}
      <div className="flex items-center gap-2 text-sm text-slate-400 mb-6">
        <Link to="/assets" className="hover:text-brand-600 flex items-center gap-1">
          <Icon name="layers" size={14} /> 美术
        </Link>
        <span>/</span>
        <span className="text-slate-600 truncate max-w-[320px]">{asset.name}</span>
      </div>

      {/* 头部信息 */}
      <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-sm mb-6">
        <div className="px-6 py-5 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <span className={cn('px-2 py-0.5 rounded text-[11px] font-medium border', tl.cls)}>{tl.text}</span>
              <h1 className="text-xl font-bold text-slate-900 break-all">{asset.name}</h1>
              <Badge variant={st} dot>{stText}</Badge>
            </div>
            {asset.description && (
              <p className="text-sm text-slate-600 mt-2 leading-6 max-w-3xl">{asset.description}</p>
            )}
            <div className="flex flex-wrap items-center gap-1.5 mt-3">
              <Icon name="folder" size={12} className="text-slate-300" />
              {asset.project_ids.length > 0 ? (
                asset.project_ids.map((pid) => (
                  <span key={pid}
                    className="inline-flex text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">
                    绑定项目 · {pid.slice(0, 8)}
                  </span>
                ))
              ) : (
                <span className="text-[11px] text-slate-300">未绑定项目</span>
              )}
              {(() => {
                const fp = asset.style_fingerprint as { style_id?: string | null; style_prompt?: string | null } | null | undefined
                if (!fp || (!fp.style_id && !fp.style_prompt)) return null
                const st = styles.find((s) => s.id === fp.style_id)
                const label = st ? st.name : (fp.style_prompt || '').trim()
                return label ? (
                  <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-violet-50 text-violet-600 border border-violet-200">
                    <Icon name="sparkles" size={11} /> 生成风格 · {label.length > 20 ? label.slice(0, 20) + '…' : label}
                  </span>
                ) : null
              })()}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <GenButton kind="cover" label="重新生成封面" icon="refresh" />
            <Button
              variant="ghost"
              size="sm"
              className="!text-rose-500 hover:!text-rose-600 hover:bg-rose-500/10"
              loading={deleting}
              onClick={handleDelete}
              leftIcon={<Icon name="trash" size={13} />}
            >
              删除资产
            </Button>
          </div>
        </div>

        {/* LLM 扩写提示词（可折叠） */}
        {asset.expanded_description && (
          <div className="border-t border-slate-100">
            <button
              onClick={() => setShowPrompt((s) => !s)}
              className="w-full px-6 py-3 flex items-center justify-between text-sm text-slate-500 hover:bg-slate-50 transition-colors"
            >
              <span className="flex items-center gap-2">
                <Icon name="sparkles" size={14} className="text-brand-600" />
                生图提示词（LLM 扩写）
              </span>
              <Icon name="chevron-down" size={14} className={cn('transition-transform', showPrompt && 'rotate-180')} />
            </button>
            {showPrompt && (
              <pre className="px-6 pb-4 max-h-[300px] overflow-y-auto text-xs leading-6 text-slate-600 whitespace-pre-wrap bg-slate-50/50">
                {asset.expanded_description}
              </pre>
            )}
          </div>
        )}
        {busy && task && (
          <div className="px-6 pb-4">
            <ProgressBar value={task.progress ?? 0} variant="brand" label={'生成中 ' + (task.progress ?? 0) + '%'} />
          </div>
        )}
      </div>

      {/* 封面 */}
      <section className="mb-6">
        <div className="flex items-center gap-2 mb-3">
          <Icon name="image" size={16} className="text-brand-600" />
          <h2 className="text-base font-semibold text-slate-800">封面图</h2>
          <span className="text-xs text-slate-400">文生图生成 · 该资产的外观/氛围基准</span>
        </div>
        {asset.cover_url ? (
          <div className="max-w-[520px]">
            <img src={asset.cover_url} alt={asset.name}
              className="w-full rounded-xl border border-slate-200 shadow-sm bg-slate-100 object-contain" />
          </div>
        ) : (
          <div className="max-w-[520px] rounded-xl border border-dashed border-slate-300 p-10 text-center text-sm text-slate-400">
            暂无封面，点击右上角「重新生成封面」
          </div>
        )}
      </section>

      {/* 角色四视图 */}
      {isCharacter && (
        <section className="mb-6">
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <Icon name="users" size={16} className="text-brand-600" />
            <h2 className="text-base font-semibold text-slate-800">角色四视图</h2>
            <span className="text-xs text-slate-400">封面驱动的图生图 · 单张四格 / 旧链路四张</span>
            <div className="ml-auto">
              <GenButton kind="fourview" label="重新生成四视图" icon="refresh" />
            </div>
          </div>
          {asset.character_sheet_url ? (
            <div className="max-w-[860px]">
              <img src={asset.character_sheet_url} alt={asset.name + ' 四视图'}
                className="w-full rounded-xl border border-slate-200 shadow-sm bg-slate-100 object-contain" />
            </div>
          ) : (asset.four_view_urls && asset.four_view_urls.length > 0) ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 max-w-[860px]">
              {asset.four_view_urls.map((u) => (
                <img key={u} src={u} alt="四视图"
                  className="w-full rounded-lg border border-slate-200 bg-slate-100 object-cover aspect-[3/4]" />
              ))}
            </div>
          ) : (
            <div className="max-w-[860px] rounded-xl border border-dashed border-slate-300 p-10 text-center text-sm text-slate-400">
              暂无四视图，点击右侧「重新生成四视图」
            </div>
          )}
        </section>
      )}

      {/* 场景多视角 */}
      {isScene && (
        <section className="mb-6">
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <Icon name="grid" size={16} className="text-brand-600" />
            <h2 className="text-base font-semibold text-slate-800">场景多视角</h2>
            <span className="text-xs text-slate-400">封面驱动的图生图 · 3x2 六格 POV 机位</span>
            <div className="ml-auto">
              <GenButton kind="multiview" label="重新生成多视角" icon="refresh" />
            </div>
          </div>
          {asset.scene_sheet_url ? (
            <div className="max-w-[860px]">
              <img src={asset.scene_sheet_url} alt={asset.name + ' 多视角'}
                className="w-full rounded-xl border border-slate-200 shadow-sm bg-slate-100 object-contain" />
            </div>
          ) : (
            <div className="max-w-[860px] rounded-xl border border-dashed border-slate-300 p-10 text-center text-sm text-slate-400">
              暂无多视角图，点击右侧「重新生成多视角」（需先有封面）
            </div>
          )}
          {Array.isArray(asset.scene_shots) && asset.scene_shots.length > 0 && (
            <details className="max-w-[860px] mt-3">
              <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-600 w-fit flex items-center gap-1">
                <Icon name="info" size={12} /> 机位配置（{asset.scene_shots.length} 格）
              </summary>
              <div className="mt-2 space-y-1.5 max-h-64 overflow-y-auto rounded-lg bg-slate-50 border border-slate-100 p-3">
                {asset.scene_shots.map((s, i) => (
                  <p key={i} className="text-[11px] text-slate-500 leading-5">
                    <span className="font-mono text-slate-400 mr-1.5">#{i + 1}</span>
                    {s.name || '视角'}：{s.view_text}
                  </p>
                ))}
              </div>
            </details>
          )}
        </section>
      )}

      {/* 其他素材（风格版本 / 参考图） */}
      {extraImgs.length > 0 && (
        <section className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <Icon name="layers" size={16} className="text-brand-600" />
            <h2 className="text-base font-semibold text-slate-800">其他素材</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 max-w-[860px]">
            {extraImgs.map((x, i) => (
              <figure key={i} className="rounded-xl border border-slate-200 overflow-hidden bg-white shadow-sm">
                <img src={x.url} alt={x.label} className="w-full aspect-square object-cover bg-slate-100" />
                <figcaption className="px-2 py-1 text-[11px] text-slate-500">{x.label}</figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}