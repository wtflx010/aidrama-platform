import { useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useTaskPoller } from '../api/useTaskPoller'
import { useBatchOp } from '../api/useBatchOp'
import { ShotStudio } from '../components/ShotStudio'
import { ContinuousFilmControl } from '../components/ContinuousFilmControl'
import { TaskCenter } from '../components/TaskCenter'
import { StylePicker } from '../components/StylePicker'
import { ContinuationDialog } from '../components/ContinuationDialog'
import { BatchGenerateDialog } from '../components/BatchGenerateDialog'
import { ExportOptionsDialog, type ExportOptions } from '../components/ExportOptionsDialog'
import { AssetPanel } from '../components/AssetPanel'
import { EvaluatedBoard } from '../components/studio/EvaluatedBoard'
import { MultilingualBoard } from '../components/studio/MultilingualBoard'
import { ReworkPanel } from '../components/studio/ReworkPanel'
import { VideoParamsEditor, type VideoParams } from '../components/VideoParamsEditor'
import type { AspectRatio, Project, ProjectStyleSelection, Segment, Episode } from '../api/types'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { Modal } from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'


/** 项目状态文案映射（与首页/其他页面一致的中文展示） */
const STATUS_MAP: Record<string, { text: string; variant: 'gray' | 'amber' | 'green' | 'red' }> = {
  draft: { text: '草稿', variant: 'gray' },
  generating: { text: '生成中', variant: 'amber' },
  done: { text: '已完成', variant: 'green' },
  failed: { text: '生成失败', variant: 'red' },
}

/** P4 屏幕尺寸可选项与可视化比例框 */
/** 「生成所有分镜」 父任务持久化 key：sessionStorage 按项目存父任务 id，切页/刷新返回后仍保持「生成中」。 */
const BATCH_STORAGE_KEY = (projectId: string) => `dsh.batchVideo.${projectId}`
/** 「超分所有分镜」父任务持久化 key（与生成分镜同款锁链）。 */
const UPSCALE_STORAGE_KEY = (projectId: string) => `dsh.batchUpscale.${projectId}`

const ASPECT_RATIOS: { value: AspectRatio; label: string; box: string }[] = [
  { value: '16:9', label: '16:9 横屏', box: 'w-10 h-[22px]' },
  { value: '9:16', label: '9:16 竖屏', box: 'w-[22px] h-10' },
  { value: '1:1', label: '1:1 方形', box: 'w-8 h-8' },
  { value: '4:3', label: '4:3 经典', box: 'w-10 h-[30px]' },
  { value: '3:4', label: '3:4 竖版', box: 'w-[30px] h-10' },
]


export default function ProjectWorkspace() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const [showSettings, setShowSettings] = useState(false)
  const [showAssets, setShowAssets] = useState(false)
  const [showEval, setShowEval] = useState(false)
  const [showMulti, setShowMulti] = useState(false)
  const [showRework, setShowRework] = useState(false)
  const [showContinuation, setShowContinuation] = useState(false)
  const [showScript, setShowScript] = useState(false)
  const [showBatch, setShowBatch] = useState(false)
  const [filmTaskId, setFilmTaskId] = useState<string | null>(null)

  // —— 批量任务锁链（生视频 / 超分共用 useBatchOp：sessionStorage 持久化 + 终态自动恢复）——
  const videoBatch = useBatchOp(id!, {
    type: 'batch_videos',
    storageKey: BATCH_STORAGE_KEY(id!),
    onAdopt: () => setShowBatch(true),
    onSettled: (t) => {
      setShowBatch(false)
      if (t.status === 'succeeded') toast.success('全部分镜视频已生成')
      else if (t.status === 'failed')
        toast.error('部分分镜视频生成失败：' + (t.error || '详情见任务中心'))
    },
  })
  const upscaleBatch = useBatchOp(id!, {
    type: 'batch_upscale_videos',
    storageKey: UPSCALE_STORAGE_KEY(id!),
    onAdopt: () => setShowBatch(true),
    onSettled: (t) => {
      setShowBatch(false)
      if (t.status === 'succeeded') toast.success('全部分镜已超分为高清')
      else if (t.status === 'failed')
        toast.error('部分分镜超分失败：' + (t.error || '详情见任务中心'))
    },
  })

  const { data: project } = useQuery({
    queryKey: ['project', id],
    queryFn: () => api.get<Project>(`/projects/${id}`),
  })
  const { data: segments = [] } = useQuery({
    queryKey: ['segments', id],
    queryFn: () => api.get<Segment[]>(`/projects/${id}/segments`),
  })
  const { data: episodes = [] } = useQuery({
    queryKey: ['episodes', id],
    queryFn: () => api.get<Episode[]>(`/projects/${id}/episodes`),
  })

  // 「生成所有分镜 / 超分所有分镜 / 合成完整视频」入口（顶栏 · 追加章节右侧）
  // 2026-09-02 批量弹窗：初始帧来源（无/手工上传/上一分镜尾帧）+ 覆盖已生成视频开关
  const [showBatchGen, setShowBatchGen] = useState(false)
  const batchAll = useMutation({
    mutationFn: (vars?: { reference_src?: string; custom_first_frame_url?: string; overwrite?: boolean }) =>
      api.post<{ task: { id: string } }>(`/projects/${id}/video/batch-generate`, vars ?? {}),
    onSuccess: (data) => {
      setShowBatch(true)
      const tid = data?.task?.id
      if (tid) videoBatch.begin(tid)
      qc.invalidateQueries({ queryKey: ['tasks', id] })
      qc.invalidateQueries({ queryKey: ['studio-clips'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const upscaleAll = useMutation({
    mutationFn: () => api.post<{ task: { id: string } }>(`/projects/${id}/video/batch-upscale`, {}),
    onSuccess: (data) => {
      setShowBatch(true)
      const tid = data?.task?.id
      if (tid) upscaleBatch.begin(tid)
      qc.invalidateQueries({ queryKey: ['tasks', id] })
      qc.invalidateQueries({ queryKey: ['studio-clips'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // 2026-09-02 导出弹窗：可选手工配音/字幕/背景音乐/音效
  const [showExportOpts, setShowExportOpts] = useState(false)
  function composeFilm(opts?: ExportOptions) {
    const q = opts
      ? `?include_voice=${opts.include_voice}&include_subtitle=${opts.include_subtitle}&burn_subtitle=${opts.burn_subtitle}&include_bgm=${opts.include_bgm}&include_sfx=${opts.include_sfx}`
      : ''
    api.post<{ id: string }>(`/projects/${id}/export${q}`, {})
      .then((r) => {
        setFilmTaskId(r.id)
        qc.invalidateQueries({ queryKey: ['studio-clips'] })
        // 集合作项目成品：直接推送到「导出」页展示/预览/下载
        navigate('/exports?project=' + id)
      })
      .catch((e: Error) => toast.error(e.message))
  }

  const exportTask = useTaskPoller(filmTaskId)
  const composeBusy = !!exportTask && (exportTask.status === 'pending' || exportTask.status === 'running')
  useEffect(() => {
    if (!exportTask) return
    if (exportTask.status === 'succeeded' || exportTask.status === 'failed' || exportTask.status === 'cancelled') {
      if (exportTask.status === 'succeeded') toast.success('成片合成完成，可在「导出」页查看或下载')
      else if (exportTask.status === 'failed') toast.error('成片合成失败，详情见任务中心')
      setFilmTaskId(null)
    }
  }, [exportTask, toast, setFilmTaskId])

  // 批量状态（busy 驱动按钮转圈禁点；终态由 useBatchOp 自动恢复并刷新视频列表）
  const batchBusy = videoBatch.busy
  const batchProgress = videoBatch.progress
  const upscaleBusy = upscaleBatch.busy
  const upscaleProgress = upscaleBatch.progress

  if (!project) return <div className="p-8 text-slate-400">加载中…</div>

  const st = STATUS_MAP[project.status] ?? { text: project.status, variant: 'gray' as const }

  return (
    <div className="h-[calc(100vh-3.75rem)] flex flex-col overflow-hidden">
      {/* P3 任务中心：全局悬浮按钮 + 抽屉，跨分镜可见 */}
      <TaskCenter projectId={project.id} />

      {/* 顶栏（固定，不随页面滚动） */}
      <div className="glass shrink-0 z-30">
        {/* 返回入口（对齐剧本详情页「剧本库 / 标题」面包屑样式） */}
        <div className="max-w-[1600px] mx-auto px-4 sm:px-6 pt-2.5">
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Link to="/" className="hover:text-brand-600 flex items-center gap-1">
              <Icon name="folder" size={14} /> 项目库
            </Link>
            <span>/</span>
            <span className="text-slate-600 truncate max-w-[300px]">{project.title}</span>
          </div>
        </div>
        <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-3 flex items-center gap-4">
          {/* 项目图标 */}
          <span className="w-10 h-10 rounded-xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center shrink-0">
            <Icon name="clapperboard" size={20} className="text-brand-600" />
          </span>

          {/* 标题信息区 */}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-lg font-bold text-slate-900 truncate">{project.title}</h1>
              {/* 屏幕尺寸 / 分辨率：放在状态（草稿）左侧；梗概简介不再展示 */}
              <Badge variant="gray" size="sm" className="shrink-0">
                <Icon name="monitor" size={11} /> {project.aspect_ratio}
              </Badge>
              <Badge variant="gray" size="sm" className="shrink-0">
                <Icon name="hd" size={11} /> {project.resolution?.toUpperCase() ?? '720P'}
              </Badge>
              <Badge variant={st.variant} dot className="shrink-0">
                {st.text}
              </Badge>
            </div>
          </div>

          {/* 操作区 */}
          <div className="flex items-center gap-2 shrink-0">
            {/* P6 章节续接追加：仅小说来源项目可追加 */}
            {project.source_novel_id && (
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Icon name="plus" size={14} />}
                onClick={() => setShowContinuation(true)}
              >
                追加章节
                {project.processed_upto_chapter ? ` · 至第${project.processed_upto_chapter}章` : ''}
              </Button>
            )}
            {/* 美术资产：放在追加章节右侧、生成所有分镜左侧 */}
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Icon name="layers" size={14} />}
              onClick={() => setShowAssets(true)}
            >
              美术资产
            </Button>
            {/* 成片评估看板（P0-1）：触发/查看四维分 + 反哺建议 + 角色一致性 */}
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Icon name="clapperboard" size={14} />}
              onClick={() => setShowEval(true)}
            >
              成片评估
            </Button>
            {/* 多语言出海（P2-5） */}
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Icon name="map-pin" size={14} />}
              onClick={() => setShowMulti(true)}
            >
              多语言
            </Button>
            {/* 修片（P1-4）：改画幅 / 换声 */}
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Icon name="settings" size={14} />}
              onClick={() => setShowRework(true)}
            >
              修片
            </Button>
            {/* 生成所有分镜 / 合成完整视频：放在美术资产右侧 */}
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Icon name="play" size={13} />}
              onClick={() => setShowBatchGen(true)}
              loading={batchBusy || batchAll.isPending}
              disabled={segments.length === 0}
              title={batchBusy ? '批量生成中，完成后自动恢复' : '生成所有分镜（初始帧来源：无/手工上传/上一分镜尾帧；可选择是否覆盖已有成片）'}
            >
              {batchBusy ? '生成中 ' + batchProgress + '%' : '生成所有分镜'}
            </Button>
            <BatchGenerateDialog
              open={showBatchGen}
              onClose={() => setShowBatchGen(false)}
              busy={batchBusy || batchAll.isPending}
              onConfirm={(st) => {
                setShowBatchGen(false)
                batchAll.mutate({
                  reference_src: st.reference_src,
                  ...(st.custom_first_frame_url ? { custom_first_frame_url: st.custom_first_frame_url } : {}),
                  overwrite: st.overwrite,
                })
              }}
            />
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Icon name="hd" size={14} />}
              onClick={() => upscaleAll.mutate()}
              loading={upscaleBusy || upscaleAll.isPending}
              disabled={segments.length === 0}
              title={upscaleBusy ? '批量超分中，完成后自动恢复' : '将已生成的分镜视频一键超分为高清（4×/2× 档位在项目设置与单镜面板可选）'}
            >
              {upscaleBusy ? '超分中 ' + upscaleProgress + '%' : '超分所有分镜'}
            </Button>
            <ContinuousFilmControl project={project} disabled={segments.length === 0} />
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Icon name="film" size={13} />}
              onClick={() => setShowExportOpts(true)}
              loading={composeBusy}
              disabled={segments.length === 0}
              title="合成完整视频（可配置 配音/字幕/背景音乐/音效；片段之间硬切、无转场动画）"
            >
              {composeBusy ? '合成中…' : '合成完整视频'}
            </Button>
            <ExportOptionsDialog
              open={showExportOpts}
              onClose={() => setShowExportOpts(false)}
              busy={composeBusy}
              onConfirm={(opts) => {
                setShowExportOpts(false)
                composeFilm(opts)
              }}
            />
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Icon name="settings" size={14} />}
              onClick={() => setShowSettings(true)}
            >
              设置
            </Button>
            {/* 剧本入口：放在设置入口右侧 */}
            {project.script && (
              <div className="relative">
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<Icon name="book" size={14} />}
                  rightIcon={
                    <Icon name="chevron-down" size={12} className={cn('transition-transform', showScript && 'rotate-180')} />
                  }
                  onClick={() => setShowScript((s) => !s)}
                >
                  剧本
                </Button>
                {showScript && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowScript(false)} />
                    <div className="absolute right-0 top-full mt-1.5 z-50 w-[560px] max-w-[80vw] bg-white border border-slate-200 rounded-xl shadow-xl overflow-hidden">
                      <div className="px-3 py-2 text-xs font-semibold text-slate-500 border-b border-slate-100 flex items-center gap-1.5">
                        <Icon name="book" size={12} className="text-brand-600" />
                        剧本正文
                      </div>
                      <pre className="text-sm text-slate-600 whitespace-pre-wrap font-mono p-3 max-h-[60vh] overflow-y-auto bg-slate-50/50">
                        {project.script}
                      </pre>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 单页正文：分镜工作台（无页签，占满整页剩余高度，不产生页面滚动） */}
      <div className="flex-1 min-h-0 w-full max-w-[1600px] mx-auto">
        <ShotStudio
          project={project}
          episodes={episodes}
          segments={segments}
          projectId={project.id}
          showBatch={showBatch}
          setShowBatch={setShowBatch}
          batchActive={batchBusy || upscaleBusy}
        />
      </div>


      {/* 美术资产弹窗：新建角色/场景/道具 + 封面/四视图/多视角 + 批量封面 + 声线（恢复被剪掉的唯一入口） */}
      {showAssets && (
        <Modal open={true} onClose={() => setShowAssets(false)} title="美术资产" size="2xl">
          <div className="max-h-[70vh] overflow-y-auto -mx-6 px-6">
            <AssetPanel projectId={project.id} />
          </div>
        </Modal>
      )}

      {/* P4 项目设置弹窗：修改屏幕尺寸与视频风格 */}
      {showSettings && project && (
        <ProjectSettingsDialog
          project={project}
          onClose={() => setShowSettings(false)}
          onSaved={() => {
            setShowSettings(false)
            qc.invalidateQueries({ queryKey: ['project', id] })
          }}
        />
      )}

      {/* P6 章节续接追加弹窗 */}
      {showContinuation && project.source_novel_id && (
        <ContinuationDialog
          novelId={project.source_novel_id}
          projectId={project.id}
          onClose={() => setShowContinuation(false)}
          onSaved={() => setShowContinuation(false)}
        />
      )}

      {/* 成片评分看板（P0-1 + P0-2）：四维分 + 反哺建议 + 角色一致性 */}
      {showEval && (
        <EvaluatedBoard
          projectId={project.id}
          episodes={episodes}
          open={showEval}
          onClose={() => setShowEval(false)}
        />
      )}

      {/* 多语言出海（P2-5） */}
      {showMulti && (
        <MultilingualBoard
          projectId={project.id}
          episodes={episodes}
          open={showMulti}
          onClose={() => setShowMulti(false)}
        />
      )}

      {/* 修片（P1-4）：改画幅 / 换声 */}
      {showRework && <ReworkPanel open={showRework} episodes={episodes} onClose={() => setShowRework(false)} />}
    </div>
  )
}

function ProjectSettingsDialog({
  project,
  onClose,
  onSaved,
}: {
  project: Project
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const [title, setTitle] = useState(project.title)
  const [synopsis, setSynopsis] = useState(project.synopsis ?? '')
  const [rules, setRules] = useState(project.rules ?? '')
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>(
    (project.aspect_ratio as AspectRatio) || '16:9',
  )
  const [style, setStyle] = useState<ProjectStyleSelection>({
    style_id: project.style_id,
    art_style_prompt: project.art_style_prompt,
  })
  const [videoParams, setVideoParams] = useState<VideoParams>(project.video_params || {})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setLoading(true)
    setError(null)
    try {
      await api.put(`/projects/${project.id}`, {
        title: title.trim(),
        synopsis: synopsis.trim() || null,
        rules: rules.trim() || null,
        aspect_ratio: aspectRatio,
        resolution: project.resolution ?? '720p',
        video_params: videoParams,
        style_id: style.style_id,
        art_style_prompt:
          style.art_style_prompt && style.art_style_prompt.trim()
            ? style.art_style_prompt.trim()
            : null,
      })
      toast.success('项目设置已保存')
      onSaved()
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
      title="项目设置"
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={save} loading={loading}>
            保存
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">项目标题</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="input-base"
          />
        </div>

        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">梗概</label>
          <textarea
            value={synopsis}
            onChange={(e) => setSynopsis(e.target.value)}
            rows={3}
            className="input-base resize-none"
          />
        </div>

        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            创作规则
            <span className="text-slate-500 font-normal">（世界观/文风/角色约束，对话中 @项目 时自动生效）</span>
          </label>
          <textarea
            value={rules}
            onChange={(e) => setRules(e.target.value)}
            rows={3}
            placeholder="例如：本剧为古风权谋，主角言必含引典；打斗描写不超过三句；旁白禁用现代词汇……"
            className="input-base resize-none"
          />
        </div>

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

        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">视频风格</label>
          <StylePicker value={style} onChange={setStyle} />
        </div>

        {/* 项目级视频参数：创建/设置项目时统一配置，批量生成按此出片（分镜级优先） */}
        <div className="rounded-xl border border-slate-200 p-3">
          <VideoParamsEditor value={videoParams} onChange={setVideoParams} title="项目视频参数" />
        </div>

        <div className="flex items-start gap-2 text-xs text-amber-600 bg-amber-500/10 border border-amber-200 rounded-lg px-3 py-2">
          <Icon name="info" size={14} className="mt-0.5 shrink-0" />
          <span>修改尺寸或风格后，已生成的关键帧/视频/资产不会自动重新生成，需手动重做。</span>
        </div>

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
