import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { AspectRatio, Project, ProjectStyleSelection, VideoResolution } from '../api/types'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { Modal } from '../components/ui/Modal'
import { EmptyState } from '../components/ui/EmptyState'
import { useToast } from '../components/ui/Toast'
import { useConfirm } from '../components/ui/ConfirmDialog'
import { StylePicker } from '../components/StylePicker'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'
import { DEFAULT_GEN_RESOLUTION, GEN_RESOLUTION_META } from '../lib/videoConfig'

/** P4 屏幕尺寸可选项与可视化比例框 */
const ASPECT_RATIOS: { value: AspectRatio; label: string; box: string }[] = [
  { value: '16:9', label: '16:9 横屏', box: 'w-10 h-[22px]' },
  { value: '9:16', label: '9:16 竖屏', box: 'w-[22px] h-10' },
  { value: '1:1', label: '1:1 方形', box: 'w-8 h-8' },
  { value: '4:3', label: '4:3 经典', box: 'w-10 h-[30px]' },
  { value: '3:4', label: '3:4 竖版', box: 'w-[30px] h-10' },
]

const STATUS_MAP: Record<
  string,
  { text: string; variant: 'gray' | 'blue' | 'green' | 'red' }
> = {
  draft: { text: '草稿', variant: 'gray' },
  generating: { text: '生成中', variant: 'blue' },
  done: { text: '已完成', variant: 'green' },
  failed: { text: '失败', variant: 'red' },
}

export default function Home() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const confirm = useConfirm()
  const [showNew, setShowNew] = useState(false)

  const {
    data: projects = [],
    isLoading,
    isError,
    error: queryError,
    refetch,
  } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get<Project[]>('/projects'),
  })

  const del = useMutation({
    mutationFn: (id: string) => api.del(`/projects/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      toast.success('项目已删除')
    },
  })

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    if (
      await confirm({
        title: '删除项目',
        message: '确认删除该项目？此操作不可撤销。项目的美术资产（含已生成图片）将一并删除。',
        danger: true,
      })
    ) {
      del.mutate(id)
    }
  }

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8 animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
            <span className="w-9 h-9 rounded-xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center">
              <Icon name="clapperboard" size={20} className="text-brand-600" />
            </span>
            项目
          </h1>
          <p className="text-sm text-slate-400 mt-1.5 ml-12">
            用一句话生成你的短剧，或手动创建项目
          </p>
        </div>
        <Button
          leftIcon={<Icon name="sparkles" size={16} />}
          onClick={() => setShowNew(true)}
        >
          新建项目
        </Button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="surface p-5 space-y-3">
              <div className="flex items-center justify-between">
                <div className="skeleton h-5 w-1/2" />
                <div className="skeleton h-5 w-16 rounded-full" />
              </div>
              <div className="skeleton h-4 w-full" />
              <div className="skeleton h-4 w-3/4" />
              <div className="flex items-center justify-between pt-2">
                <div className="skeleton h-3 w-28" />
                <div className="skeleton h-3 w-10" />
              </div>
            </div>
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          icon="alert-circle"
          title="项目列表加载失败"
          description={
            (queryError instanceof Error && queryError.message) ||
            '后端暂不可达，请稍后重试'
          }
          action={
            <Button leftIcon={<Icon name="refresh" size={16} />} onClick={() => refetch()}>
              重新加载
            </Button>
          }
        />
      ) : projects.length === 0 ? (
        <EmptyState
          icon="clapperboard"
          title="还没有项目"
          description="点击「新建项目」，用一句话生成你的第一部短剧"
          action={
            <Button
              leftIcon={<Icon name="sparkles" size={16} />}
              onClick={() => setShowNew(true)}
            >
              新建项目
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((p) => {
            const st = STATUS_MAP[p.status] ?? {
              text: p.status,
              variant: 'gray' as const,
            }
            return (
              <Card
                key={p.id}
                hover
                className="p-5 group"
                onClick={() => navigate(`/projects/${p.id}`)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="w-9 h-9 rounded-lg bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center shrink-0">
                      <Icon name="film" size={18} className="text-brand-600" />
                    </span>
                    <h3 className="font-medium text-slate-900 truncate">{p.title}</h3>
                  </div>
                  <Badge variant={st.variant} dot>
                    {st.text}
                  </Badge>
                </div>
                <p className="text-sm text-slate-500 mt-3 line-clamp-2 min-h-[2.5rem]">
                  {p.synopsis || '无梗概'}
                </p>
                <div className="flex items-center justify-between mt-4 text-xs text-slate-400">
                  <span className="flex items-center gap-1">
                    <Icon name="clock" size={12} />
                    {new Date(p.created_at).toLocaleString('zh-CN')}
                  </span>
                  <button
                    onClick={(e) => handleDelete(e, p.id)}
                    className="text-slate-400 hover:text-rose-500 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                    aria-label="删除项目"
                  >
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      {showNew && (
        <NewProjectDialog
          onClose={() => setShowNew(false)}
          onCreated={(id) => navigate(`/projects/${id}`)}
        />
      )}
    </div>
  )
}

function NewProjectDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const toast = useToast()
  const [mode, setMode] = useState<'ai' | 'manual'>('ai')
  const [synopsis, setSynopsis] = useState('')
  const [title, setTitle] = useState('')
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>('16:9')
  // 视频分辨率（480P/720P/768P）：后续生成的视频统一按此档位执行（单一来源 lib/videoConfig）
  const RESOLUTIONS: { value: VideoResolution; label: string; desc: string }[] = (
    Object.values(GEN_RESOLUTION_META) as { value: VideoResolution; label: string; desc?: string }[]
  ).map((m) => ({ value: m.value, label: m.label, desc: m.desc ?? '' }))
  const [resolution, setResolution] = useState<VideoResolution>(DEFAULT_GEN_RESOLUTION)
  // 分镜时长上限（秒）：5/10/15，AI 生成时 LLM 按镜头内容在 1~上限内配置每镜时长
  const PER_DURATIONS = [5, 10, 15]
  const [perDuration, setPerDuration] = useState(15)
  const [style, setStyle] = useState<ProjectStyleSelection>({
    style_id: null,
    art_style_prompt: null,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setError(null)
    setLoading(true)
    try {
      let p: Project
      // 风格字段：自定义模式下若文本为空则不传
      const styleBody = {
        style_id: style.style_id,
        art_style_prompt:
          style.art_style_prompt && style.art_style_prompt.trim()
            ? style.art_style_prompt.trim()
            : null,
      }
      if (mode === 'ai') {
        if (!synopsis.trim()) {
          setError('请输入一句话梗概')
          setLoading(false)
          return
        }
        p = await api.post<Project>('/projects/ai-generate', {
          synopsis: synopsis.trim(),
          aspect_ratio: aspectRatio,
          resolution,
          per_duration: perDuration,
          ...styleBody,
        })
      } else {
        if (!title.trim()) {
          setError('请输入标题')
          setLoading(false)
          return
        }
        p = await api.post<Project>('/projects', {
          title: title.trim(),
          synopsis: synopsis.trim() || undefined,
          aspect_ratio: aspectRatio,
          resolution,
          ...styleBody,
        })
      }
      toast.success('项目创建成功')
      onCreated(p.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="新建项目"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} loading={loading}>
            {mode === 'ai' ? '生成' : '创建'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2 p-1 bg-white border border-slate-200 rounded-xl">
          <button
            onClick={() => setMode('ai')}
            className={cn(
              'flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all',
              mode === 'ai'
                ? 'bg-gradient-brand text-white shadow-glow-sm'
                : 'text-slate-500 hover:text-slate-800',
            )}
          >
            <Icon name="sparkles" size={14} />
            AI 生成
          </button>
          <button
            onClick={() => setMode('manual')}
            className={cn(
              'flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all',
              mode === 'manual'
                ? 'bg-gradient-brand text-white shadow-glow-sm'
                : 'text-slate-500 hover:text-slate-800',
            )}
          >
            <Icon name="pencil" size={14} />
            手动新建
          </button>
        </div>

        {mode === 'manual' && (
          <div>
            <label className="text-xs text-slate-400 mb-1.5 block">项目标题</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="输入项目标题"
              className="input-base"
            />
          </div>
        )}

        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            {mode === 'ai' ? '一句话梗概' : '梗概（可选）'}
          </label>
          <textarea
            value={synopsis}
            onChange={(e) => setSynopsis(e.target.value)}
            placeholder={
              mode === 'ai'
                ? '一句话梗概，例如：一个外卖员在暴雨夜救下一只流浪猫…'
                : '梗概（可选）'
            }
            rows={4}
            className="input-base resize-none"
          />
        </div>

        {/* P4 屏幕尺寸选择 */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">屏幕尺寸</label>
          <div className="grid grid-cols-5 gap-1.5">
            {ASPECT_RATIOS.map((ar) => (
              <button
                key={ar.value}
                type="button"
                onClick={() => setAspectRatio(ar.value)}
                title={ar.label}
                className={cn(
                  'flex flex-col items-center gap-1.5 py-2.5 rounded-lg border transition-all',
                  aspectRatio === ar.value
                    ? 'border-brand-500 bg-brand-500/10 ring-1 ring-brand-500/30'
                    : 'border-slate-200 hover:border-brand-300 hover:bg-slate-50',
                )}
              >
                <span
                  className={cn(
                    'border-2 rounded-sm',
                    ar.box,
                    aspectRatio === ar.value
                      ? 'border-brand-500 bg-brand-500/20'
                      : 'border-slate-300',
                  )}
                />
                <span
                  className={cn(
                    'text-[10px] font-medium',
                    aspectRatio === ar.value ? 'text-brand-600' : 'text-slate-500',
                  )}
                >
                  {ar.value}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* 视频分辨率选择（480P/720P，后续生成的视频统一按此档位执行） */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">分辨率</label>
          <div className="grid grid-cols-2 gap-1.5">
            {RESOLUTIONS.map((r) => (
              <button
                key={r.value}
                type="button"
                onClick={() => setResolution(r.value)}
                className={cn(
                  'flex flex-col items-start gap-0.5 px-3 py-2 rounded-lg border text-sm font-medium transition-all',
                  resolution === r.value
                    ? 'border-brand-500 bg-brand-500/10 ring-1 ring-brand-500/30'
                    : 'border-slate-200 hover:border-brand-300 hover:bg-slate-50',
                )}
              >
                <span className={resolution === r.value ? 'text-brand-600' : 'text-slate-600'}>
                  {r.label}
                </span>
                <span className="text-[10px] font-normal text-slate-400">{r.desc}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 分镜时长上限选择（AI 生成时 LLM 按镜头内容配置每镜时长，不超过该上限） */}
        {mode === 'ai' && (
          <div>
            <label className="text-xs text-slate-400 mb-1.5 block">分镜时长上限</label>
            <div className="grid grid-cols-4 gap-1.5">
              {PER_DURATIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setPerDuration(d)}
                  className={cn(
                    'py-1.5 rounded-lg border text-sm font-medium transition-all',
                    perDuration === d
                      ? 'border-brand-500 bg-brand-500/10 text-brand-600 ring-1 ring-brand-500/30'
                      : 'border-slate-200 text-slate-600 hover:border-brand-300 hover:bg-slate-50',
                  )}
                >
                  {d}s
                </button>
              ))}
            </div>
            <p className="text-xs text-slate-400 mt-1.5 flex items-start gap-1">
              <Icon name="info" size={12} className="mt-0.5 shrink-0" />
              每个分镜对应一张关键帧 + 一段视频；LLM 按镜头内容配置每镜时长（1s~上限），台词过长会自动拆分为多个分镜。
            </p>
          </div>
        )}

        {/* P4 视频风格选择 */}
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">视频风格</label>
          <StylePicker value={style} onChange={setStyle} compact />
        </div>

        {mode === 'ai' && (
          <p className="text-xs text-slate-400 flex items-center gap-1.5">
            <Icon name="info" size={12} className="text-brand-600" />
            LLM 会自动生成标题、剧本与分镜列表，风格将注入全链路生成
          </p>
        )}

        {error && (
          <div className="flex items-start gap-2 text-sm text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg px-3 py-2">
            <Icon name="alert-circle" size={14} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>
    </Modal>
  )
}
