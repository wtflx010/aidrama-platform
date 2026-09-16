/**
 * AI 视频（Video Lab）独立功能页（2026-08-11）
 *
 * 顶层导航独立入口（/videolab），不依赖项目：
 * - 纯文生 / 首尾帧 / 参考视频+图片混合（自动路由 R2V/FL2VA）
 * - 「参考项目」为可选：选择项目后可多选其资产作为参考图（锁角色/场景一致性）；
 *   不选项目则纯用上传素材
 * - 左半部分：素材区（所选项目的资产多选参考）
 * - 右半部分：结果区（生成视频列表，播放/删除/重试/进度）
 * - 提示词对话框：提示词 + LLM 优化 + 负面词 + 首尾帧/参考图/参考视频上传（左上角小图标）
 *   + 画面比例/时长选择 + 生成
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, uploadVideoLabFile } from '../api/client'
import { useTaskPoller, formatWait } from '../api/useTaskPoller'
import type {
  Asset,
  Project,
  VideoDraft,
  VideoDraftCreate,
  VideoDraftEnhanceOut,
  VideoDraftRegenResp,
} from '../api/types'
import { Button } from './ui/Button'
import { Badge } from './ui/Badge'
import { Modal } from './ui/Modal'
import { EmptyState } from './ui/EmptyState'
import { ProgressBar } from './ui/ProgressBar'
import { useToast } from './ui/Toast'
import { Icon } from '../lib/icons'
import { GEN_RESOLUTIONS } from '../lib/videoConfig'
import { cn } from '../lib/cn'

const RATIOS = [
  { value: '16:9', label: '16:9 横屏' },
  { value: '9:16', label: '9:16 竖屏' },
  { value: '1:1', label: '1:1 方形' },
  { value: '4:3', label: '4:3 经典' },
  { value: '3:4', label: '3:4 竖版' },
]
const DURATIONS = [5, 8, 10, 15]
// 2026-08-23 二采验证：生成档位（空 = 跟随项目/模型默认）
// 生成档位（'' = 跟随项目；档位来自统一配置 lib/videoConfig 的 GEN_RESOLUTIONS，含 0.1MP~1.0MP）
const RESOLUTION_OPTIONS = [
  { value: '', label: '默认（跟随项目）' },
  ...GEN_RESOLUTIONS.map((r) => ({ value: r, label: r })),
]
const TYPE_LABEL: Record<string, string> = { character: '角色', scene: '场景', prop: '道具' }
const TYPE_ORDER = ['character', 'scene', 'prop'] as const
const STATUS_VARIANT: Record<string, 'gray' | 'amber' | 'green' | 'red'> = {
  pending: 'amber',
  running: 'amber',
  succeeded: 'green',
  failed: 'red',
}
const STATUS_LABEL: Record<string, string> = {
  pending: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '失败',
}
const TASK_ENDED = new Set(['succeeded', 'failed', 'cancelled'])
const isBusy = (s: string) => s === 'pending' || s === 'running'

export default function VideoLab() {
  const qc = useQueryClient()
  const toast = useToast()
  const [showDialog, setShowDialog] = useState(false)
  const [playUrl, setPlayUrl] = useState<string | null>(null)
  const [refProjectId, setRefProjectId] = useState<string | null>(null)
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(new Set())
  const [refreshTick, setRefreshTick] = useState(0)
  const [busyIds, setBusyIds] = useState<Record<string, boolean>>({})

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get<Project[]>('/projects'),
  })
  const { data: drafts = [] } = useQuery({
    queryKey: ['video-drafts', refreshTick],
    queryFn: () => api.get<VideoDraft[]>('/video-drafts'),
    // 有排队/生成中的草稿时持续刷新
    refetchInterval: (q) => (q.state.data ?? []).some((d) => isBusy(d.status)) ? 3000 : false,
  })
  const { data: assets = [] } = useQuery({
    queryKey: ['assets', refProjectId],
    queryFn: () => api.get<Asset[]>(`/projects/${refProjectId}/assets`),
    enabled: !!refProjectId,
  })

  const del = useMutation({
    mutationFn: (id: string) => api.del(`/video-drafts/${id}`),
    onSuccess: () => {
      toast.success('已删除')
      qc.invalidateQueries({ queryKey: ['video-drafts'] })
    },
  })

  async function handleDelete(d: VideoDraft) {
    if (window.confirm('删除该视频草稿？')) del.mutate(d.id)
  }

  const groupedAssets = TYPE_ORDER.map((t) => ({
    type: t,
    label: TYPE_LABEL[t],
    items: assets.filter((a) => a.type === t),
  })).filter((g) => g.items.length > 0)

  function toggleAsset(id: string) {
    setSelectedAssets((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // 切换参考项目时清空已选资产
  useEffect(() => {
    setSelectedAssets(new Set())
  }, [refProjectId])

  // 任务状态接管后清理「刚提交」的 busy 标记：草稿进入 running（进度条接管）
  // 或 failed/cancelled（终态）后该 id 不再需要按钮 loading 态。
  useEffect(() => {
    const ids = Object.keys(busyIds)
    if (!ids.length) return
    const stale = ids.filter((id) => {
      const d = drafts.find((x) => x.id === id)
      return d && (isBusy(d.status) || d.status === 'failed')
    })
    if (stale.length) {
      setBusyIds((prev) => {
        const n = { ...prev }
        stale.forEach((k) => delete n[k])
        return n
      })
    }
  }, [drafts, busyIds])

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-6 animate-fade-in">
      {/* 页头：标题 + 参考项目选择 */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2.5">
            <span className="w-9 h-9 rounded-xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center">
              <Icon name="video" size={18} className="text-brand-600" />
            </span>
            AI 视频
          </h1>
          <p className="text-sm text-slate-400 mt-1.5 ml-12">
            纯文生 / 首尾帧 / 参考视频+图片混合，独立于项目
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400 shrink-0">参考项目（可选）</label>
          <select
            value={refProjectId ?? ''}
            onChange={(e) => setRefProjectId(e.target.value || null)}
            className="input-base w-56"
          >
            <option value="">不使用资产参考</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.title}</option>
            ))}
          </select>
          <Button
            leftIcon={<Icon name="plus" size={14} />}
            onClick={() => setShowDialog(true)}
          >
            新建提示词
          </Button>
        </div>
      </div>

      <div className="flex h-[calc(100vh-12rem)] border border-slate-200 rounded-xl overflow-hidden">
        {/* 左：素材区（所选项目资产多选参考） */}
        <div className="w-72 shrink-0 border-r border-slate-200 flex flex-col">
          <div className="p-3 border-b border-slate-200 flex items-center justify-between">
            <span className="text-sm font-medium text-slate-700 flex items-center gap-1.5">
              <Icon name="layers" size={15} />
              素材参考
            </span>
            {selectedAssets.size > 0 && (
              <Badge variant="purple" size="sm">{selectedAssets.size} 已选</Badge>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-4">
            {!refProjectId ? (
              <p className="text-xs text-slate-400 leading-relaxed">
                未选择参考项目。纯文生/首尾帧/参考素材可直接生成；如需用角色/场景/道具资产做参考，请在上方选择一个项目。
              </p>
            ) : groupedAssets.length === 0 ? (
              <p className="text-xs text-slate-400 leading-relaxed">
                该项目暂无美术资产，可在项目内「美术资产」页签创建角色/场景/道具后回来选择。
              </p>
            ) : (
              <>
                {groupedAssets.map((g) => (
                  <div key={g.type}>
                    <div className="text-xs text-slate-400 font-medium mb-1.5">{g.label}</div>
                    <div className="grid grid-cols-2 gap-2">
                      {g.items.map((a) => {
                        const selected = selectedAssets.has(a.id)
                        return (
                          <button
                            key={a.id}
                            onClick={() => toggleAsset(a.id)}
                            title={a.name}
                            className={cn(
                              'relative rounded-lg border overflow-hidden transition-all text-left',
                              selected
                                ? 'border-brand-500 ring-2 ring-brand-500/40'
                                : 'border-slate-200 hover:border-brand-300',
                            )}
                          >
                            {a.cover_url ? (
                              <img src={a.cover_url} alt={a.name} className="w-full aspect-[4/5] object-cover" />
                            ) : (
                              <div className="w-full aspect-[4/5] bg-slate-100 flex items-center justify-center text-slate-300">
                                <Icon name="image" size={20} />
                              </div>
                            )}
                            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-1.5 pb-1 pt-3">
                              <span className="text-[11px] text-white truncate block">{a.name}</span>
                            </div>
                            {selected && (
                              <div className="absolute top-1 right-1 w-5 h-5 rounded-full bg-brand-500 text-white flex items-center justify-center">
                                <Icon name="check" size={12} />
                              </div>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ))}
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  勾选资产后，生成时会自动取角色四视图/场景封面作为参考图（≤9 张），锁定角色与场景一致性。
                </p>
              </>
            )}
          </div>
        </div>

        {/* 右：结果区（生成视频列表） */}
        <div className="flex-1 overflow-y-auto p-4">
          {drafts.length === 0 ? (
            <EmptyState
              icon="clapperboard"
              title="还没有生成视频"
              description="点击「新建提示词」编写提示词，可选首尾帧、参考图/参考视频与资产参考，一键生成"
            />
          ) : (
            <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
              {drafts.map((d) => (
                <DraftCard
                  key={d.id}
                  draft={d}
                  onPlay={() => d.video_url && setPlayUrl(d.video_url)}
                  onDelete={() => handleDelete(d)}
                  onRegenerate={() => {
                    setBusyIds((p) => ({ ...p, [d.id]: true }))
                    api.post<{ task_id: string }>(`/video-drafts/${d.id}/generate`)
                      .then(() => {
                        toast.success('已重新提交生成')
                        qc.invalidateQueries({ queryKey: ['video-drafts'] })
                      })
                      .catch((e) => {
                        setBusyIds((p) => { const n = { ...p }; delete n[d.id]; return n })
                        toast.error((e as Error).message)
                      })
                  }}
                  onRegen768={() => {
                    setBusyIds((p) => ({ ...p, [d.id]: true }))
                    api.post<VideoDraftRegenResp>(`/video-drafts/${d.id}/regenerate`)
                      .then(() => {
                        toast.success('已提交超分，完成后将就地升级为 1080p')
                        qc.invalidateQueries({ queryKey: ['video-drafts'] })
                      })
                      .catch((e) => {
                        setBusyIds((p) => { const n = { ...p }; delete n[d.id]; return n })
                        toast.error((e as Error).message)
                      })
                  }}
                  onRefresh={() => setRefreshTick((t) => t + 1)}
                  regenBusy={!!busyIds[d.id]}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 提示词对话框 */}
      {showDialog && (
        <VideoPromptDialog
          projectId={refProjectId}
          initialAssetIds={[...selectedAssets]}
          onClose={() => setShowDialog(false)}
          onGenerated={() => {
            setShowDialog(false)
            setRefreshTick((t) => t + 1)
            qc.invalidateQueries({ queryKey: ['video-drafts'] })
          }}
        />
      )}

      {/* 视频播放 */}
      {playUrl && (
        <Modal open onClose={() => setPlayUrl(null)} title="视频预览" size="xl">
          <video src={playUrl} controls autoPlay className="w-full rounded-xl bg-black" />
        </Modal>
      )}
    </div>
  )
}

function DraftCard({
  draft,
  onPlay,
  onDelete,
  onRegenerate,
  onRegen768,
  onRefresh,
  regenBusy,
}: {
  draft: VideoDraft
  onPlay: () => void
  onDelete: () => void
  onRegenerate: () => void
  onRegen768: () => void
  onRefresh: () => void
  regenBusy: boolean
}) {
  const poll = useTaskPoller(
    isBusy(draft.status) && draft.task_id ? draft.task_id : null,
    { detailed: true },
  )
  const task = poll.task
  // 任务终态但草稿仍显示运行中 → 立即刷新草稿列表
  useEffect(() => {
    if (task && TASK_ENDED.has(task.status) && isBusy(draft.status)) onRefresh()
  }, [task?.status, draft.status, onRefresh])

  const refCount =
    (draft.first_frame_url ? 1 : 0) +
    (draft.last_frame_url ? 1 : 0) +
    (draft.ref_image_urls ?? []).length +
    (draft.ref_video_urls ?? []).length

  return (
    <div className="glass rounded-xl overflow-hidden border border-slate-200 flex flex-col">
      {/* 视频/占位 */}
      <div className="relative aspect-video bg-slate-950 group">
        {draft.video_url ? (
          <video
            src={draft.video_url}
            preload="metadata"
            muted
            playsInline
            className="w-full h-full object-contain"
            onClick={onPlay}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-600">
            <Icon name="video" size={28} className="opacity-40" />
          </div>
        )}
        {draft.video_url && (
          <button
            onClick={onPlay}
            className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <span className="w-12 h-12 rounded-full bg-black/60 text-white flex items-center justify-center backdrop-blur-sm">
              <Icon name="play" size={20} className="ml-0.5" />
            </span>
          </button>
        )}
        <div className="absolute top-2 right-2 flex gap-1">
          {draft.resolution && (
            <Badge variant="purple" size="sm">{draft.resolution}</Badge>
          )}
          {draft.base_draft_id && (
            <Badge variant="blue" size="sm">超分</Badge>
          )}
          <Badge variant="gray" size="sm">{draft.duration}s</Badge>
          <Badge variant="gray" size="sm">{draft.aspect_ratio}</Badge>
        </div>
        <div className="absolute top-2 left-2">
          <Badge variant={STATUS_VARIANT[draft.status]} size="sm">
            {STATUS_LABEL[draft.status]}
          </Badge>
        </div>
      </div>

      {/* 内容 */}
      <div className="p-3 flex-1 flex flex-col gap-2">
        <p className="text-xs text-slate-600 line-clamp-2 min-h-[2rem]">
          {draft.enhanced_prompt || draft.prompt}
        </p>
        {refCount > 0 && (
          <div className="flex flex-wrap gap-1">
            {draft.first_frame_url && <Badge variant="blue" size="sm">首帧</Badge>}
            {draft.last_frame_url && <Badge variant="blue" size="sm">尾帧</Badge>}
            {(draft.ref_image_urls ?? []).length > 0 && (
              <Badge variant="amber" size="sm">参考图 {(draft.ref_image_urls ?? []).length}</Badge>
            )}
            {(draft.ref_video_urls ?? []).length > 0 && (
              <Badge variant="amber" size="sm">参考视频 {(draft.ref_video_urls ?? []).length}</Badge>
            )}
            {(draft.asset_refs ?? []).length > 0 && (
              <Badge variant="gray" size="sm">资产 {(draft.asset_refs ?? []).length}</Badge>
            )}
          </div>
        )}

        {/* 进度 / 错误 */}
        {(isBusy(draft.status) || regenBusy) && (
          <ProgressBar
            value={task?.progress ?? 5}
            label={
              task
                ? `已等待 ${formatWait(poll.waitSeconds)}`
                : regenBusy
                  ? '已提交，等待任务接入…'
                  : '排队中…'
            }
          />
        )}
        {draft.status === 'failed' && (
          <div className="text-[11px] text-rose-500 leading-snug break-all">
            {draft.error || '生成失败'}
          </div>
        )}

        <div className="mt-auto flex items-center justify-between pt-1">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-400">
              {new Date(draft.created_at).toLocaleString()}
            </span>
            {!isBusy(draft.status) && (
              <Button variant="ghost" size="sm" className="text-slate-500" loading={regenBusy}
                leftIcon={<Icon name="refresh" size={13} />} onClick={onRegenerate}>
                重新生成
              </Button>
            )}
          </div>
          <div className="flex gap-1.5">
            {draft.status === 'succeeded' && !draft.base_draft_id && draft.resolution !== '1080p' && (
              <Button variant="outline" size="sm" className="text-blue-600 border-blue-300 hover:bg-blue-500/10"
                loading={regenBusy} leftIcon={<Icon name="zap" size={13} />} onClick={onRegen768}>
                超分到 1080p
              </Button>
            )}
            <Button variant="ghost" size="sm" className="text-rose-500 hover:text-rose-600"
              leftIcon={<Icon name="trash" size={13} />} onClick={onDelete}>
              删除
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ─── 提示词对话框 ─────────────────────────────────────────── */

type RefKind = 'first_frame' | 'last_frame' | 'ref_image' | 'ref_video'

function VideoPromptDialog({
  projectId,
  initialAssetIds,
  onClose,
  onGenerated,
}: {
  projectId: string | null
  initialAssetIds: string[]
  onClose: () => void
  onGenerated: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [prompt, setPrompt] = useState('')
  const [negative, setNegative] = useState('')
  const [firstFrame, setFirstFrame] = useState<string | null>(null)
  const [lastFrame, setLastFrame] = useState<string | null>(null)
  const [refImages, setRefImages] = useState<string[]>([])
  const [refVideos, setRefVideos] = useState<string[]>([])
  const [assetIds, setAssetIds] = useState<string[]>(initialAssetIds)
  const [ratio, setRatio] = useState('16:9')
  const [duration, setDuration] = useState(5)
  const [resolution, setResolution] = useState<string | null>(null)
  const [enhancing, setEnhancing] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<{ kind: RefKind; input: HTMLInputElement } | null>(null)

  const { data: assets = [] } = useQuery({
    queryKey: ['assets', projectId],
    queryFn: () => api.get<Asset[]>(`/projects/${projectId}/assets`),
    enabled: !!projectId,
  })

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    const kind = fileRef.current?.kind
    if (!file || !kind) return
    setError(null)
    try {
      const url = await uploadVideoLabFile(file)
      switch (kind) {
        case 'first_frame':
          setFirstFrame(url)
          break
        case 'last_frame':
          setLastFrame(url)
          break
        case 'ref_image':
          setRefImages((p) => (p.length >= 9 ? p : [...p, url]))
          break
        case 'ref_video':
          setRefVideos((p) => (p.length >= 3 ? p : [...p, url]))
          break
      }
      toast.success('上传成功')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  function triggerUpload(kind: RefKind) {
    const input = fileRef.current?.input ?? document.createElement('input')
    input.type = 'file'
    input.accept = kind === 'ref_video' ? 'video/mp4,video/webm,video/mov,video/m4v' : 'image/*'
    input.onchange = (e) => handleFile(e as unknown as React.ChangeEvent<HTMLInputElement>)
    if (!fileRef.current) {
      fileRef.current = { kind, input }
      document.body.appendChild(input)
    }
    fileRef.current.kind = kind
    input.click()
  }

  function toggleAsset(id: string) {
    setAssetIds((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  }

  function buildBody(): VideoDraftCreate {
    return {
      project_id: projectId,
      prompt: prompt.trim(),
      negative_prompt: negative.trim() || null,
      first_frame_url: firstFrame,
      last_frame_url: lastFrame,
      ref_image_urls: refImages,
      ref_video_urls: refVideos,
      asset_refs: assetIds,
      aspect_ratio: ratio,
      duration,
      resolution,
    }
  }

  async function enhance() {
    if (!prompt.trim()) {
      setError('请先填写提示词')
      return
    }
    setEnhancing(true)
    setError(null)
    try {
      const res = await api.post<VideoDraftEnhanceOut>('/video-drafts/enhance', buildBody())
      setPrompt(res.enhanced_prompt)
      if (res.negative_prompt) setNegative(res.negative_prompt)
      toast.success('提示词已优化')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setEnhancing(false)
    }
  }

  async function generate() {
    if (!prompt.trim()) {
      setError('请填写提示词')
      return
    }
    setGenerating(true)
    setError(null)
    try {
      const draft = await api.post<VideoDraft>('/video-drafts', buildBody())
      await api.post<{ task_id: string }>(`/video-drafts/${draft.id}/generate`)
      toast.success('已提交生成任务')
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
      onGenerated()
    } catch (err) {
      setError((err as Error).message)
      setGenerating(false)
    }
  }

  const refCount = (firstFrame ? 1 : 0) + (lastFrame ? 1 : 0) + refImages.length + refVideos.length

  return (
    <Modal
      open
      onClose={onClose}
      title="AI 视频提示词"
      size="xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button
            variant="outline"
            loading={enhancing}
            leftIcon={<Icon name="wand" size={14} />}
            onClick={enhance}
          >
            LLM 优化
          </Button>
          <Button loading={generating} leftIcon={<Icon name="zap" size={14} />} onClick={generate}>
            生成视频
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* 参考素材上传区（首帧/尾帧/参考图/参考视频横向一行 + 已选缩略） */}
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <UploadIcon
              icon="image"
              label="首帧"
              active={!!firstFrame}
              onClick={() => triggerUpload('first_frame')}
              onClear={firstFrame ? () => setFirstFrame(null) : undefined}
            />
            <UploadIcon
              icon="image"
              label="尾帧"
              active={!!lastFrame}
              onClick={() => triggerUpload('last_frame')}
              onClear={lastFrame ? () => setLastFrame(null) : undefined}
            />
            <UploadIcon
              icon="image"
              label={`参考图${refImages.length ? ` ${refImages.length}/9` : ''}`}
              active={refImages.length > 0}
              onClick={() => triggerUpload('ref_image')}
            />
            <UploadIcon
              icon="video"
              label={`参考视频${refVideos.length ? ` ${refVideos.length}/3` : ''}`}
              active={refVideos.length > 0}
              onClick={() => triggerUpload('ref_video')}
            />
          </div>

          <div className="min-h-[72px]">
            <div className="flex flex-wrap gap-2">
              {firstFrame && <RefThumb url={firstFrame} label="首帧" onRemove={() => setFirstFrame(null)} />}
              {lastFrame && <RefThumb url={lastFrame} label="尾帧" onRemove={() => setLastFrame(null)} />}
              {refImages.map((u, i) => (
                <RefThumb key={u} url={u} label={`参考图${i + 1}`}
                  onRemove={() => setRefImages((p) => p.filter((x) => x !== u))} />
              ))}
              {refVideos.map((u, i) => (
                <RefThumb key={u} url={u} label={`参考视频${i + 1}`} video
                  onRemove={() => setRefVideos((p) => p.filter((x) => x !== u))} />
              ))}
              {refCount === 0 && (
                <p className="text-xs text-slate-400 leading-relaxed self-center">
                  可选上传首帧/尾帧图片、参考图（≤9）与参考视频（≤3）——上传后可混合驱动生成；
                  未上传则纯文生视频。
                </p>
              )}
            </div>
          </div>
        </div>

        {/* 提示词 */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">提示词</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={9}
            placeholder="描述想要生成的画面：主体、动作、镜头运动、环境、氛围、声音…（可用「LLM 优化」自动扩写）"
            className="input-base resize-none font-mono text-[13px]"
          />
        </div>

        {/* 负面词 */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            负面提示词 <span className="text-slate-300">（可留空，LLM 优化时自动生成）</span>
          </label>
          <textarea
            value={negative}
            onChange={(e) => setNegative(e.target.value)}
            rows={3}
            placeholder="不希望出现的内容，如：low quality, blurry, watermark…"
            className="input-base resize-none font-mono text-[13px]"
          />
        </div>

        {/* 资产参考多选 */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            资产参考{' '}
            <span className="text-slate-300">
              {projectId ? '（取角色四视图/场景封面）' : '（未选参考项目，不可用）'}
            </span>
          </label>
          {!projectId ? (
            <p className="text-xs text-slate-400">
              可在页面左上角「参考项目」选择一个项目后使用其资产作为参考。
            </p>
          ) : assets.length === 0 ? (
            <p className="text-xs text-slate-400">该项目暂无资产，可跳过。</p>
          ) : (
            <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto">
              {assets.map((a) => (
                <button
                  key={a.id}
                  onClick={() => toggleAsset(a.id)}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs transition-all',
                    assetIds.includes(a.id)
                      ? 'border-brand-500 bg-brand-500/10 text-brand-600'
                      : 'border-slate-200 text-slate-500 hover:border-brand-300',
                  )}
                >
                  {a.cover_url && (
                    <img src={a.cover_url} alt="" className="w-4 h-4 rounded-full object-cover" />
                  )}
                  <span className="max-w-[120px] truncate">{a.name}</span>
                  <span className="text-[10px] text-slate-400">({TYPE_LABEL[a.type]})</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* 参数：比例 + 时长 */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-slate-400 mb-1.5 block">画面比例</label>
            <div className="grid grid-cols-5 gap-1.5">
              {RATIOS.map((r) => (
                <button
                  key={r.value}
                  onClick={() => setRatio(r.value)}
                  title={r.label}
                  className={cn(
                    'py-1.5 rounded-lg border text-xs transition-all',
                    ratio === r.value
                      ? 'border-brand-500 bg-brand-500/10 text-brand-600'
                      : 'border-slate-200 text-slate-500 hover:border-brand-300',
                  )}
                >
                  {r.value}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1.5 block">时长（秒）</label>
            <div className="grid grid-cols-5 gap-1.5">
              {DURATIONS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDuration(d)}
                  className={cn(
                    'py-1.5 rounded-lg border text-xs transition-all',
                    duration === d
                      ? 'border-brand-500 bg-brand-500/10 text-brand-600'
                      : 'border-slate-200 text-slate-500 hover:border-brand-300',
                  )}
                >
                  {d}s
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* 生成清晰度（2026-08-25：480p 草稿最快，出片后可一键「超分到 1080p」） */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            生成清晰度{' '}
            <span className="text-slate-300">
              （480p 草稿最快；出片后卡片上可「超分到 1080p」，就地覆盖当前视频）
            </span>
          </label>
          <select
            value={resolution ?? ''}
            onChange={(e) => setResolution(e.target.value || null)}
            className="input-base w-48"
          >
            {RESOLUTION_OPTIONS.map((r) => (
              <option key={r.value || 'default'} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>

        {error && (
          <div className="flex items-start gap-2 text-sm text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg px-3 py-2">
            <Icon name="alert-circle" size={14} className="mt-0.5 shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}
      </div>
    </Modal>
  )
}

function UploadIcon({
  icon,
  label,
  active,
  onClick,
  onClear,
}: {
  icon: string
  label: string
  active: boolean
  onClick: () => void
  onClear?: () => void
}) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onClick}
        title={label}
        className={cn(
          'w-12 h-12 rounded-lg border flex flex-col items-center justify-center gap-0.5 transition-all',
          active
            ? 'border-brand-500 bg-brand-500/10 text-brand-600'
            : 'border-slate-200 text-slate-400 hover:border-brand-300 hover:text-brand-500',
        )}
      >
        <Icon name={icon} size={16} />
        <span className="text-[9px] font-medium leading-none">{label}</span>
      </button>
      {onClear && (
        <button
          type="button"
          onClick={onClear}
          title={`移除${label}`}
          className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-rose-500 text-white flex items-center justify-center shadow-sm hover:bg-rose-600"
        >
          <Icon name="x" size={10} />
        </button>
      )}
    </div>
  )
}

function RefThumb({
  url,
  label,
  video,
  onRemove,
}: {
  url: string
  label: string
  video?: boolean
  onRemove: () => void
}) {
  return (
    <div className="relative group">
      <div className="w-20 h-20 rounded-lg overflow-hidden border border-slate-200 bg-slate-950">
        {video ? (
          <video src={url} preload="metadata" muted className="w-full h-full object-cover" />
        ) : (
          <img src={url} alt={label} className="w-full h-full object-cover" />
        )}
      </div>
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent px-1 pb-0.5">
        <span className="text-[9px] text-white truncate block">{label}</span>
      </div>
      <button
        type="button"
        onClick={onRemove}
        className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-rose-500 text-white flex items-center justify-center shadow-sm hover:bg-rose-600"
      >
        <Icon name="x" size={10} />
      </button>
    </div>
  )
}
