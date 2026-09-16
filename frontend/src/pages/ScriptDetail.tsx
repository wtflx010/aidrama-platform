/**
 * 剧本详情页：剧本总览 + 分镜时间轴 + 镜头资产绑定 + AI 润色。
 *
 * 数据来源：
 * - GET /novels/{id} → 剧本总览（raw_text / 状态 / 关联项目）
 * - 若已生成项目：GET /projects/{pid}/episodes、/projects/{pid}/segments
 *   → 按幕分组渲染分镜时间轴，每镜展示已绑定的角色/场景/道具（可编辑）
 * 操作：
 * - 整体润色：POST /novels/{id}/polish（同步 LLM，更新剧本文本）
 * - 单镜润色：POST /segments/{id}/polish（同步 LLM，更新该镜描述/对白/旁白）
 * - 生成项目：头部「生成项目」仅此一个入口 → 配置弹窗（屏幕尺寸/清晰度/帧率/
 *   推理步数/CFG/Turbo/种子/分镜时长/风格）→ POST /novels/{id}/adapt 异步改编。
 *   配置与项目详情「项目设置」一致（2026-08-24 唯一入口收敛）。
 */
import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '../api/client'
import { useTaskPoller } from '../api/useTaskPoller'
import { DEFAULT_GEN_RESOLUTION, GEN_RESOLUTIONS, type GenResolution } from '../lib/videoConfig'
import type { AspectRatio, Episode, NovelDetail, ProjectStyleSelection, Segment } from '../api/types'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { Modal } from '../components/ui/Modal'
import { StylePicker } from '../components/StylePicker'
import { EmptyState } from '../components/ui/EmptyState'
import { ProgressBar } from '../components/ui/ProgressBar'
import { useToast } from '../components/ui/Toast'
import { SegmentAssetBinder } from '../components/SegmentAssetBinder'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

const STATUS_MAP: Record<string, { text: string; variant: 'gray' | 'blue' | 'green' | 'red' }> = {
  pending: { text: '新剧本', variant: 'gray' },
  analyzing: { text: '分析中', variant: 'blue' },
  done: { text: '已就绪', variant: 'green' },
  failed: { text: '失败', variant: 'red' },
}

/** P4 屏幕尺寸可选项与可视化比例框（同项目详情/首页） */
const ASPECT_RATIOS: { value: AspectRatio; label: string; box: string }[] = [
  { value: '16:9', label: '16:9 横屏', box: 'w-10 h-[22px]' },
  { value: '9:16', label: '9:16 竖屏', box: 'w-[22px] h-10' },
  { value: '1:1', label: '1:1 方形', box: 'w-8 h-8' },
  { value: '4:3', label: '4:3 经典', box: 'w-10 h-[30px]' },
  { value: '3:4', label: '3:4 竖版', box: 'w-[30px] h-10' },
]

/** 项目级视频生成参数选项（与 VideoParamsEditor 一致；空值 = 跟随默认） */
const GEN_FPS = ['16', '24', '30', '60']
const GEN_STEPS = ['4', '6', '8', '16', '20']
const GEN_CFG = ['1.0', '2.0', '3.0', '4.0', '5.0']
const GEN_TURBO = [
  { value: 'high', label: 'Turbo 高' },
  { value: 'mid', label: 'Turbo 中' },
  { value: 'low', label: 'Turbo 低' },
]

export default function ScriptDetail() {
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const [showBody, setShowBody] = useState(false)
  const [polishingSegments, setPolishingSegments] = useState<Record<string, boolean>>({})
  // 幕折叠状态：存折叠的幕 id（默认全部展开；「全部折叠」后手动展开单个）
  const [collapsedEps, setCollapsedEps] = useState<Set<string>>(new Set())

  const { data: novel } = useQuery({
    queryKey: ['novel', id],
    queryFn: () => api.get<NovelDetail>(`/novels/${id}`),
  })

  const projectId = novel?.project_id ?? null
  const { data: episodes = [] } = useQuery({
    queryKey: ['episodes', projectId ?? ''],
    queryFn: () => api.get<Episode[]>(`/projects/${projectId}/episodes`),
    enabled: !!projectId,
  })
  const { data: segments = [] } = useQuery({
    queryKey: ['segments', projectId ?? ''],
    queryFn: () => api.get<Segment[]>(`/projects/${projectId}/segments`),
    enabled: !!projectId,
  })
  const polishAll = useMutation({
    mutationFn: () => api.post(`/novels/${id}/polish`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['novel', id] })
      qc.invalidateQueries({ queryKey: ['novels'] })
      toast.success('整体润色完成，剧本文本已更新')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  function handleSegmentPolish(segId: string) {
    if (polishingSegments[segId]) return
    setPolishingSegments((p) => ({ ...p, [segId]: true }))
    api
      .post(`/segments/${segId}/polish`)
      .then(() => {
        qc.invalidateQueries({ queryKey: ['segments', projectId ?? ''] })
        toast.success('分镜润色完成')
      })
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setPolishingSegments((p) => ({ ...p, [segId]: false })))
  }

  // ── 生成分镜（2026-08-30 手工导入剧本：剧本文本 → 分镜预览）──
  // 导入剧本本身已写好的分镜（分镜N（X秒）等格式）→ 后端直落按剧本执行，不重新生成
  const [shotTaskId, setShotTaskId] = useState<string | null>(null)
  const shotTask = useTaskPoller(shotTaskId)
  const shotBusy = !!shotTask && (shotTask.status === 'pending' || shotTask.status === 'running')
  const [shotError, setShotError] = useState<string | null>(null)

  // 分镜生成成功 → 刷新剧本（shot_plan 就绪后渲染分镜预览）；失败 → 提示
  useEffect(() => {
    if (shotTask?.status === 'succeeded') {
      qc.invalidateQueries({ queryKey: ['novel', id] })
      toast.success('分镜已生成，可在下方查看分镜预览')
      setShotTaskId(null)
      setShotError(null)
    } else if (shotTask?.status === 'failed' || shotTask?.status === 'cancelled') {
      toast.error('分镜生成失败：' + (shotTask.error || '详情见任务中心'))
      setShotTaskId(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shotTask?.status])

  function startGenerateShotPlan() {
    if (shotBusy || shotTaskId) return
    setShotError(null)
    api
      .post<{ task_id: string; already_running?: boolean }>('/novels/' + id + '/shot-plan')
      .then((r) => setShotTaskId(r.task_id))
      .catch((e: Error) => setShotError(e.message))
  }

  // ── 生成项目（无相关项目时可从这里直接触达改编任务）──
  const [genTaskId, setGenTaskId] = useState<string | null>(null)
  const genTask = useTaskPoller(genTaskId)
  const genBusy = !!genTask && (genTask.status === 'pending' || genTask.status === 'running')
  const [genError, setGenError] = useState<string | null>(null)
  const [genOpen, setGenOpen] = useState(false)
  const [genAspect, setGenAspect] = useState<AspectRatio>('16:9')
  const [genRes, setGenRes] = useState<GenResolution>(DEFAULT_GEN_RESOLUTION)
  const [genDuration, setGenDuration] = useState(15)
  const [genStyle, setGenStyle] = useState<ProjectStyleSelection>({ style_id: null, art_style_prompt: null })
  // 项目级视频生成参数（对齐项目详情：帧率/推理步数/CFG/Turbo/种子）
  const [genFps, setGenFps] = useState('24')
  const [genSteps, setGenSteps] = useState('')
  const [genCfg, setGenCfg] = useState('')
  const [genTurbo, setGenTurbo] = useState('')
  const [genSeed, setGenSeed] = useState('')

  // 生成成功 → 自动跳转到生成的项目工作台（navigate 必须在 effect 中调用，不能在渲染期间）
  useEffect(() => {
    if (genTask?.status === 'succeeded') {
      const m = (genTask.result_url || '').split('/projects/')[1]
      if (m) {
        navigate(`/projects/${m}`)
        setGenTaskId(null)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genTask?.status])

  function startGenerateProject() {
    if (genBusy || genTaskId) return
    setGenError(null)
    setGenOpen(false)
    api
      .post<{ task_id: string }>(`/novels/${id}/adapt`, {
        per_duration: genDuration,
        style_id: genStyle.style_id,
        art_style_prompt: genStyle.art_style_prompt || null,
        aspect_ratio: genAspect,
        resolution: genRes,
        video_params: {
          fps: genFps,
          res: genRes,
          video_size: genAspect,
          steps: genSteps || undefined,
          cfg: genCfg || undefined,
          seed: genSeed || undefined,
          turbo: genTurbo || undefined,
        },
      })
      .then((r) => {
        setGenTaskId(r.task_id)
        qc.invalidateQueries({ queryKey: ['novel', id] })
      })
      .catch((e: Error) => setGenError(e.message))
  }

  if (!novel) {
    return (
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-12">
        <EmptyState icon="book" title="剧本不存在或已删除" description="返回剧本库查看其他剧本" />
      </div>
    )
  }

  const st = STATUS_MAP[novel.analysis_status] ?? { text: novel.analysis_status, variant: 'gray' as const }
  // 2026-08-27：头部统计优先用「已确认的分镜预览」(shot_plan)——未建项目时项目分镜为空，
  // 旧逻辑显示 0 分镜/0 秒，与智能体统计对不上；有项目后以项目分镜为准（复用保证两者一致）。
  const previewSegs = (novel.shot_plan?.episodes ?? []).flatMap((e) => (e.segments ?? []) as Array<{ duration?: number | null }>)
  const statSegs = projectId && segments.length > 0 ? (segments as Array<{ duration?: number | null }>) : previewSegs
  const totalDuration = statSegs.reduce((a, s) => a + (s.duration || 0), 0)

  function toggleEp(epId: string) {
    setCollapsedEps((prev) => {
      const next = new Set(prev)
      if (next.has(epId)) next.delete(epId)
      else next.add(epId)
      return next
    })
  }

  function collapseAll() {
    setCollapsedEps(new Set(episodes.map((e) => e.id)))
  }

  function expandAll() {
    setCollapsedEps(new Set())
  }

  const allCollapsed = episodes.length > 0 && episodes.every((e) => collapsedEps.has(e.id))

  return (
    <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 animate-fade-in">
      <div className="flex items-center gap-2 text-sm text-slate-400 mb-6">
        <Link to="/novels" className="hover:text-brand-600 flex items-center gap-1">
          <Icon name="book" size={14} /> 剧本库
        </Link>
        <span>/</span>
        <span className="text-slate-600 truncate max-w-[300px]">{novel.title}</span>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-sm mb-6">
        <div className="px-6 py-5 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="w-10 h-10 rounded-xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center">
                <Icon name="book" size={20} className="text-brand-600" />
              </span>
              <h1 className="text-xl font-bold text-slate-900 break-all">{novel.title}</h1>
              <Badge variant={st.variant} dot>{st.text}</Badge>
              {novel.project_id && (
                <Badge variant="green">
                  <Icon name="check" size={12} />
                  已生成项目
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-4 text-xs text-slate-400 mt-3">
              <span className="flex items-center gap-1">
                <Icon name="layers" size={12} /> {novel.chapters_count} 章 / {segments.length} 个分镜
              </span>
              <span className="flex items-center gap-1">
                <Icon name="type" size={12} /> {novel.word_count.toLocaleString()} 字
              </span>
              <span className="flex items-center gap-1">
                <Icon name="eye" size={12} /> 总时长约 {Math.round(totalDuration)}s
              </span>
              <span className="flex items-center gap-1">
                <Icon name="clock" size={12} /> {new Date(novel.created_at).toLocaleString('zh-CN')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="outline"
              leftIcon={<Icon name="sparkles" size={16} />}
              onClick={() => polishAll.mutate()}
              loading={polishAll.isPending}
              disabled={!novel.raw_text?.trim()}
            >
              整体润色
            </Button>
            {!novel.project_id && (
              <Button
                variant="outline"
                leftIcon={<Icon name="film" size={16} />}
                onClick={startGenerateShotPlan}
                loading={shotBusy}
                disabled={shotBusy || !!shotTaskId}
                title={novel.shot_plan ? '剧本已生成过分镜，可重新生成' : '剧本文本 → 分镜预览（导入剧本自带分镜时按剧本直落）'}
              >
                {shotBusy ? '生成分镜中 ' + (shotTask?.progress ?? 0) + '%' : novel.shot_plan ? '重新生成分镜' : '生成分镜'}
              </Button>
            )}
            {novel.project_id ? (
              <Button
                leftIcon={<Icon name="clapperboard" size={16} />}
                onClick={() => navigate(`/projects/${novel.project_id}`)}
              >
                进入项目
              </Button>
            ) : (
              <Button
                variant="success"
                leftIcon={<Icon name="clapperboard" size={16} />}
                onClick={() => setGenOpen(true)}
                loading={genBusy}
                disabled={genBusy || !!genTaskId}
              >
                {genBusy ? `生成项目中 ${genTask?.progress ?? 0}%` : '生成项目'}
              </Button>
            )}
          </div>
          {shotError && (
            <div className="w-full mt-3 flex items-center gap-1.5 text-xs text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg px-3 py-2">
              <Icon name="alert-circle" size={12} className="shrink-0" />
              <span>{shotError}</span>
            </div>
          )}
          {genError && (
            <div className="w-full mt-3 flex items-center gap-1.5 text-xs text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg px-3 py-2">
              <Icon name="alert-circle" size={12} className="shrink-0" />
              <span>{genError}</span>
            </div>
          )}
        </div>
        {polishAll.isPending && (
          <div className="px-6 pb-4">
            <ProgressBar value={0} variant="brand" label="AI 正在润色整体剧本（保持剧情不变）…" />
          </div>
        )}
        <div className="border-t border-slate-100">
          <button
            onClick={() => setShowBody((s) => !s)}
            className="w-full px-6 py-3 flex items-center justify-between text-sm text-slate-500 hover:bg-slate-50 transition-colors"
          >
            <span className="flex items-center gap-2">
              <Icon name="book" size={14} className="text-brand-600" />
              剧本正文（{novel.word_count.toLocaleString()} 字）
            </span>
            <Icon name="chevron-down" size={14} className={cn('transition-transform', showBody && 'rotate-180')} />
          </button>
          {showBody && (
            <div className="px-6 pb-5 max-h-[420px] overflow-y-auto">
              <pre className="text-sm leading-7 text-slate-600 whitespace-pre-wrap font-sans bg-slate-50 border border-slate-100 rounded-xl p-4">
                {novel.raw_text}
              </pre>
            </div>
          )}
        </div>
      </div>

      {!projectId && (
        <div>
          {/* 分镜预览（写剧本后预生成；确认后才生成项目） */}
          {novel.shot_plan && novel.shot_plan.segment_count > 0 && (
            <div className="mb-6">
              <div className="flex items-center gap-2.5 mb-3">
                <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                  <Icon name="film" size={18} className="text-brand-600" /> 分镜预览
                </h2>
                <span className="text-xs text-slate-400">
                  {novel.shot_plan.episode_count} 幕 · {novel.shot_plan.segment_count} 分镜（MiniMax H3 规范）
                </span>
                {novel.shot_plan.source === 'direct' ? (
                  <Badge variant="green" size="sm">按导入剧本直落</Badge>
                ) : (
                  <Badge variant="blue" size="sm">AI 生成</Badge>
                )}
              </div>
              <div className="space-y-4">
                {novel.shot_plan.episodes.map((ep) => (
                  <div key={ep.index} className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                    <div className="px-4 py-2.5 bg-slate-50/60 border-b border-slate-100 font-semibold text-slate-800 text-sm flex items-center gap-2">
                      <span className="w-6 h-6 rounded-md bg-brand-500/10 border border-brand-200 flex items-center justify-center text-brand-600 text-xs font-bold shrink-0">{ep.index + 1}</span>
                      <span className="truncate">{ep.title}</span>
                      <span className="text-xs font-normal text-slate-400 shrink-0">{ep.segments.length} 镜</span>
                    </div>
                    <div className="px-4 py-3 space-y-1.5">
                      {ep.segments.map((s, si) => (
                        <div key={si} className="rounded-lg border border-slate-100 px-3 py-2">
                          <div className="flex items-center gap-2 text-xs flex-wrap">
                            <span className="font-mono text-slate-400 shrink-0">{ep.index + 1}-{s.index ?? si + 1}</span>
                            {s.title && <span className="font-medium text-slate-700">{s.title}</span>}
                            {s.shot_type && <Badge variant="blue" size="sm">{s.shot_type}</Badge>}
                            {s.camera && <Badge variant="gray" size="sm">{s.camera}</Badge>}
                            <span className="ml-auto text-slate-400 shrink-0">{s.duration ?? "?"}s</span>
                          </div>
                          {s.description && <p className="text-xs text-slate-500 mt-1 line-clamp-3">{s.description}</p>}
                          {s.narration && (
                            <p className="text-xs text-violet-700/90 mt-1"><span className="text-violet-500 font-medium">旁白：</span>{s.narration}</p>
                          )}
                          {(s.dialogue_lines && s.dialogue_lines.length > 0) && (
                            <div className="mt-1 space-y-0.5">
                              {(s.dialogue_lines as Array<{ speaker?: string; text?: string }>).map((dl, di) => (
                                <p key={di} className="text-xs text-slate-700">
                                  <span className="text-brand-600 font-medium">{dl.speaker}</span>：{dl.text}
                                </p>
                              ))}
                            </div>
                          )}
                          {s.characters && (s.characters as string[]).length > 0 && (
                            <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px]">
                              <Icon name="users" size={11} className="text-slate-400" />
                              {(s.characters as string[]).map((ch, ci) => (
                                <span key={ci} className="px-1.5 py-0.5 rounded bg-brand-500/10 text-brand-600 border border-brand-200">{ch}</span>
                              ))}
                              {(s.scene ? Array.isArray(s.scene) ? s.scene : [s.scene] : []).map((sc, ci) => (
                                <span key={'s' + ci} className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 border border-emerald-200">{String(sc)}</span>
                              ))}
                              {(s.props && (s.props as string[]).length > 0) && (s.props as string[]).map((pp, ci) => (
                                <span key={'p' + ci} className="px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-600 border border-orange-200">{pp}</span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

        </div>
      )}
      {projectId && (
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
            <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2.5">
              <Icon name="film" size={18} className="text-brand-600" />
              分镜时间轴
              <span className="text-xs font-normal text-slate-400 ml-1">
                {episodes.length} 幕 · {segments.length} 分镜 · 资产自动绑定
              </span>
            </h2>
            {segments.length > 0 && (
              <div className="flex items-center gap-2">
                <p className="text-xs text-slate-400 hidden sm:block">
                  共 {segments.length} 镜，总计约 {Math.round(totalDuration / 60)} 分钟
                </p>
                <button
                  onClick={allCollapsed ? expandAll : collapseAll}
                  className="text-xs text-brand-600 hover:text-brand-700 font-medium flex items-center gap-1 transition-colors"
                >
                  <Icon name="chevron-down" size={13} className={cn('transition-transform', !allCollapsed && 'rotate-180')} />
                  {allCollapsed ? '全部展开' : '全部折叠'}
                </button>
              </div>
            )}
          </div>

          {segments.length === 0 ? (
            <EmptyState icon="film" title="该项目还没有分镜" description="可在项目页通过生成分镜后再回到本页查看" />
          ) : (
            <div className="space-y-6">
              {episodes.map((ep) => {
                const epSegs = segments
                  .filter((s) => s.episode_id === ep.id)
                  .sort((a, b) => a.index - b.index)
                if (epSegs.length === 0) return null
                const collapsed = collapsedEps.has(ep.id)
                return (
                  <div key={ep.id} className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                    <button
                      onClick={() => toggleEp(ep.id)}
                      className="w-full px-5 py-3.5 flex items-center justify-between border-b border-slate-100 bg-slate-50/60 hover:bg-slate-100/70 transition-colors text-left"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <span className="w-7 h-7 rounded-lg bg-brand-500/10 border border-brand-200 flex items-center justify-center text-brand-600 font-bold text-xs shrink-0">
                          {ep.index + 1}
                        </span>
                        <h3 className="font-semibold text-slate-800 truncate">{ep.title}</h3>
                        {ep.synopsis && (
                          <span className="text-xs text-slate-400 truncate hidden md:inline"> · {ep.synopsis}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2.5 shrink-0">
                        <Badge variant="gray" size="sm">{epSegs.length} 镜</Badge>
                        <Icon name="chevron-down" size={14} className={cn('text-slate-400 transition-transform', collapsed && 'rotate-180')} />
                      </div>
                    </button>
                    {collapsed ? null : (
                    <div className="relative px-6 py-5">
                      <div className="absolute left-[31px] top-0 bottom-0 w-px bg-slate-100" />
                      <div className="space-y-5">
                        {epSegs.map((s) => {
                          const isPolishing = polishingSegments[s.id]
                          return (
                            <div key={s.id} className="relative pl-10">
                              <span className="absolute left-[23px] top-4 w-4 h-4 rounded-full border-2 border-brand-200 bg-white flex items-center justify-center">
                                <span className="w-1.5 h-1.5 rounded-full bg-brand-500" />
                              </span>
                              <div className="rounded-xl border border-slate-200 hover:border-brand-300/70 transition-colors">
                                <div className="px-4 py-3 flex items-center gap-2 flex-wrap">
                                  <span className="text-xs font-mono text-slate-400 w-8">{ep.index + 1}-{s.index}</span>
                                  {s.title && (
                                    <span className="text-sm font-semibold text-slate-800">{s.title}</span>
                                  )}
                                  {s.shot_type && <Badge variant="blue" size="sm">{s.shot_type}</Badge>}
                                  {s.camera && <Badge variant="gray" size="sm">{s.camera}</Badge>}
                                  {s.emotion && <Badge variant="purple" size="sm">{s.emotion}</Badge>}
                                  <span className="text-xs text-slate-400 ml-auto shrink-0">{s.duration}s</span>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    leftIcon={<Icon name="sparkles" size={12} />}
                                    onClick={() => handleSegmentPolish(s.id)}
                                    disabled={isPolishing}
                                  >
                                    {isPolishing ? '润色中…' : '润色'}
                                  </Button>
                                </div>
                                <div className="px-4 pb-3 space-y-2">
                                  <p className="text-sm text-slate-600 leading-6">{s.description}</p>
                                  {(s.dialogue_lines?.length > 0 || s.dialogue) && (
                                    <div className="rounded-lg bg-slate-50 border border-slate-100 px-3 py-2 space-y-1">
                                      <div className="flex items-center gap-1.5 text-xs font-semibold text-brand-600 mb-1">
                                        <Icon name="mic" size={12} className="mt-0.5 shrink-0" />
                                        对白
                                      </div>
                                      {Array.isArray(s.dialogue_lines) && s.dialogue_lines.length > 0
                                        ? s.dialogue_lines.map((dl, i) => (
                                            <p key={i} className="text-sm text-slate-700 flex items-start gap-2">
                                              <span className="text-brand-600 font-medium shrink-0">{dl.speaker}</span>
                                              <span className="flex-1">{dl.text}</span>
                                            </p>
                                          ))
                                        : s.dialogue && (
                                            <p className="text-sm text-slate-700 whitespace-pre-wrap">{s.dialogue}</p>
                                          )}
                                    </div>
                                  )}
                                  {s.narration && (
                                    <div className="rounded-lg bg-violet-500/[0.06] border border-violet-200/70 px-3 py-2">
                                      <div className="flex items-center gap-1.5 text-xs font-semibold text-violet-700 mb-1">
                                        <Icon name="eye" size={12} className="mt-0.5 shrink-0" />
                                        旁白
                                      </div>
                                      <p className="text-sm text-violet-800/90 leading-6 whitespace-pre-wrap">{s.narration}</p>
                                    </div>
                                  )}
                                  <SegmentAssetBinder segment={s} projectId={projectId} />
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
      {/* 生成项目配置（唯一入口：屏幕尺寸/清晰度/帧率/推理步数/CFG/Turbo/种子/分镜时长/风格） */}
      <Modal
        open={genOpen}
        onClose={() => setGenOpen(false)}
        title="生成项目"
        size="lg"
        footer={
          <>
            <Button variant="ghost" onClick={() => setGenOpen(false)}>取消</Button>
            <Button onClick={startGenerateProject} loading={genBusy} disabled={genBusy || !!genTaskId}>确认生成</Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">屏幕尺寸</label>
            <div className="grid grid-cols-5 gap-1.5">
              {ASPECT_RATIOS.map((ar) => (
                <button
                  key={ar.value}
                  type="button"
                  onClick={() => setGenAspect(ar.value)}
                  title={ar.label}
                  className={cn(
                    'flex flex-col items-center gap-1.5 py-2.5 rounded-lg border transition-all',
                    genAspect === ar.value
                      ? 'border-brand-500 bg-brand-500/10 ring-1 ring-brand-500/30'
                      : 'border-slate-200 hover:border-brand-300 hover:bg-slate-50',
                  )}
                >
                  <span
                    className={cn(
                      'border-2 rounded-sm',
                      ar.box,
                      genAspect === ar.value ? 'border-brand-500 bg-brand-500/20' : 'border-slate-300',
                    )}
                  />
                  <span
                    className={cn(
                      'text-[10px] font-medium',
                      genAspect === ar.value ? 'text-brand-600' : 'text-slate-500',
                    )}
                  >
                    {ar.value}
                  </span>
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">清晰度</label>
              <div className="grid grid-cols-2 gap-1.5">
                {GEN_RESOLUTIONS.map((r) => (
                  <button key={r} type="button" onClick={() => setGenRes(r)}
                    className={cn("py-1.5 rounded-lg border text-sm font-medium transition-colors",
                      genRes === r ? "bg-brand-500 text-white border-brand-500" : "bg-white text-slate-600 border-slate-200 hover:border-brand-400")}>
                    {r}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">帧率</label>
              <select value={genFps} onChange={(e) => setGenFps(e.target.value)} className="input-base text-sm">
                {GEN_FPS.map((f) => <option key={f} value={f}>{f}fps</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">推理步数</label>
              <select value={genSteps} onChange={(e) => setGenSteps(e.target.value)} className="input-base text-sm">
                <option value="">跟随默认</option>
                {GEN_STEPS.map((s) => <option key={s} value={s}>{s}步{s === '4' ? ' Turbo' : ''}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">引导强度 CFG</label>
              <select value={genCfg} onChange={(e) => setGenCfg(e.target.value)} className="input-base text-sm">
                <option value="">跟随默认</option>
                {GEN_CFG.map((c) => <option key={c} value={c}>{c}{c === '1.0' ? '（默认）' : ''}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">Turbo 模式</label>
              <select value={genTurbo} onChange={(e) => setGenTurbo(e.target.value)} className="input-base text-sm">
                <option value="">跟随默认</option>
                {GEN_TURBO.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">
                种子 Seed
                <span className="text-slate-400 font-normal">（留空 = 随机）</span>
              </label>
              <input
                type="number"
                value={genSeed}
                onChange={(e) => setGenSeed(e.target.value)}
                placeholder="留空 = 随机"
                className="input-base text-sm"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">分镜时长上限</label>
            <select value={genDuration} onChange={(e) => setGenDuration(Number(e.target.value))} className="input-base text-sm">
              {[5, 10, 15].map((d) => <option key={d} value={d}>{d}s / 镜</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">视频风格</label>
            <StylePicker value={genStyle} onChange={setGenStyle} compact />
          </div>
        </div>
      </Modal>

    </div>
  )
}