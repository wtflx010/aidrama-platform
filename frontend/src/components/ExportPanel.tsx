/**
 * 成片导出（剧集导出，2026-08-13）：
 * - 左侧导航栏：项目（顶级，可折叠展开/收起）→ 幕（二级，按第 N 集顺序）
 * - 右侧：选中幕的导出区（幕信息头 + 视频准备状态 + 导出开关 + 进度 + 成片卡片）
 * - 成片卡片：封面缩略图、集数/标题、导出时间、时长、文件大小、状态、播放/下载/删除
 * - 同一幕多次导出覆盖旧成片（后端 export_episode 覆盖语义，每幕只保留最新成片）
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Episode, EpisodeExportOut, Project, Task, TaskStatus } from '../api/types'
import { Button } from './ui/Button'
import { Badge } from './ui/Badge'
import { ProgressBar } from './ui/ProgressBar'
import { useConfirm } from './ui/ConfirmDialog'
import { useToast } from './ui/Toast'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

const TERMINAL: Set<TaskStatus> = new Set(['succeeded', 'failed', 'cancelled'])
const isBusy = (s?: TaskStatus) => s === 'pending' || s === 'running'

type BadgeVariant = 'gray' | 'blue' | 'green' | 'red' | 'purple' | 'orange' | 'indigo' | 'amber'

const STATUS_BADGE: Record<TaskStatus, { text: string; variant: BadgeVariant }> = {
  pending: { text: '排队中', variant: 'gray' },
  running: { text: '导出中', variant: 'blue' },
  succeeded: { text: '已完成', variant: 'green' },
  failed: { text: '失败', variant: 'red' },
  cancelled: { text: '已取消', variant: 'gray' },
}

function formatSize(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatDuration(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec <= 0) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return m > 0 ? `${m}m${String(s).padStart(2, '0')}s` : `${s}s`
}

/** 封面 URL：成片 .mp4 → 同名 _cover.jpg（后端 _extract_cover_frame 约定） */
function coverUrl(resultUrl: string | null | undefined): string | null {
  return resultUrl?.endsWith('.mp4') ? resultUrl.slice(0, -4) + '_cover.jpg' : null
}

function EpisodeExportArea({ episode }: { episode: Episode }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  // 默认关配音：视频生成模型（Agnes）原生输出角色语音/旁白，导出直接用视频自带音轨
  const [includeVoice, setIncludeVoice] = useState(false)
  const [includeSubtitle, setIncludeSubtitle] = useState(true)
  const [includeBgm, setIncludeBgm] = useState(true)
  const [includeSfx, setIncludeSfx] = useState(true)
  const [durationSec, setDurationSec] = useState<number | null>(null)

  const queryKey = ['episode-export', episode.id] as const
  const { data, isError } = useQuery({
    queryKey,
    queryFn: () => api.get<EpisodeExportOut>(`/episodes/${episode.id}/export`),
    // 任务活跃时轮询：导出完成后拿到最终 file_size
    refetchInterval: (q) => {
      const t = q.state.data?.task
      return t && !TERMINAL.has(t.status) ? 2000 : false
    },
  })

  const task: Task | null = data?.task ?? null
  const status = task?.status
  const progress = task?.progress ?? 0
  const busy = isBusy(status)

  const exp = useMutation({
    mutationFn: () =>
      api.post<Task>(
        `/episodes/${episode.id}/export?include_voice=${includeVoice}&include_subtitle=${includeSubtitle}&include_bgm=${includeBgm}&include_sfx=${includeSfx}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey })
      toast.success('已开始导出本集')
    },
    onError: (e: Error) => toast.error(`导出失败：${e.message}`),
  })

  const del = useMutation({
    mutationFn: (taskId: string) => api.del(`/exports/${taskId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey })
      toast.success('成片已删除')
    },
    onError: (e: Error) => toast.error(`删除失败：${e.message}`),
  })

  const exportable = data?.exportable ?? false
  const reason = data?.reason
  const cover = coverUrl(task?.result_url)
  const fileSize = status === 'succeeded' ? data?.file_size : null
  const finishedAt = status === 'succeeded' ? task?.finished_at || task?.created_at : null

  const Toggle = ({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) => (
    <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-brand-500 w-4 h-4 rounded"
      />
      {label}
    </label>
  )

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4">
      {/* 幕信息头 + 准备状态 */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-slate-900 truncate">
            第{episode.index + 1}集 {episode.title || `第${episode.index + 1}集`}
          </h3>
          {episode.synopsis && (
            <p className="text-xs text-slate-400 mt-0.5 line-clamp-2">{episode.synopsis}</p>
          )}
        </div>
        {status ? (
          <Badge variant={STATUS_BADGE[status].variant} dot={busy}>
            {STATUS_BADGE[status].text}
          </Badge>
        ) : data ? (
          <Badge variant={exportable ? 'green' : 'gray'}>{exportable ? '视频就绪' : '暂无视频'}</Badge>
        ) : null}
      </div>

      {/* 导出选项开关 */}
      <div className="flex flex-wrap items-center gap-4 mb-3 pb-3 border-b border-slate-200">
        <Toggle label="配音" checked={includeVoice} onChange={setIncludeVoice} />
        <Toggle label="字幕" checked={includeSubtitle} onChange={setIncludeSubtitle} />
        <Toggle label="背景音乐" checked={includeBgm} onChange={setIncludeBgm} />
        <Toggle label="音效" checked={includeSfx} onChange={setIncludeSfx} />
        <Button
          size="sm"
          variant="primary"
          loading={exp.isPending}
          disabled={busy || exp.isPending || !exportable}
          onClick={() => exp.mutate()}
        >
          {busy ? '导出中…' : task ? '重新导出' : '导出本集'}
        </Button>
      </div>

      {exp.isError && (
        <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg p-2 mb-2">
          <Icon name="alert-circle" size={14} className="text-rose-500 mt-0.5 shrink-0" />
          {(exp.error as Error).message}
        </div>
      )}
      {isError && !data && (
        <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg p-2 mb-2">
          <Icon name="alert-circle" size={14} className="text-rose-500 mt-0.5 shrink-0" />
          加载导出状态失败，请刷新重试
        </div>
      )}

      {status === 'failed' && task?.error && (
        <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg p-2 mb-2">
          <Icon name="alert-circle" size={14} className="text-rose-500 mt-0.5 shrink-0" />
          {task.error}
        </div>
      )}

      {/* 无视频提示 */}
      {!status && data && !exportable && reason && (
        <div className="flex items-center gap-2 text-xs text-slate-400 bg-slate-50 border border-slate-200 rounded-lg p-2 mb-2">
          <Icon name="info" size={14} className="text-slate-400 shrink-0" />
          {reason}
        </div>
      )}

      {busy && (
        <ProgressBar
          value={progress}
          label={status === 'running' ? `导出中 ${progress}%` : '排队中…'}
          variant="brand"
        />
      )}

      {/* 成片卡片 */}
      {status === 'succeeded' && task?.result_url && (
        <div className="mt-2 flex gap-3 border border-slate-200 rounded-lg p-3">
          <video
            src={task.result_url}
            preload="metadata"
            muted
            onLoadedMetadata={(e) => setDurationSec(e.currentTarget.duration)}
            onClick={() => window.open(task.result_url!, '_blank')}
            title="点击播放"
            className="w-44 h-28 shrink-0 object-cover rounded-md bg-black cursor-pointer"
          />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-slate-800 truncate">
                第{episode.index + 1}集 {episode.title || `第${episode.index + 1}集`}
              </span>
              <Badge variant="green">已完成</Badge>
            </div>
            <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-500">
              <div className="flex gap-1"><span className="text-slate-400">导出时间</span>{finishedAt ? new Date(finishedAt).toLocaleString() : '—'}</div>
              <div className="flex gap-1"><span className="text-slate-400">时长</span>{formatDuration(durationSec)}</div>
              <div className="flex gap-1"><span className="text-slate-400">文件大小</span>{formatSize(fileSize)}</div>
              <div className="flex gap-1"><span className="text-slate-400">状态</span>已完成</div>
            </dl>
            <div className="mt-2 flex items-center gap-2">
              <a href={task.result_url} download>
                <Button size="sm" variant="success" leftIcon={<Icon name="download" size={14} />}>
                  下载
                </Button>
              </a>
              <a href={task.result_url} target="_blank" rel="noreferrer">
                <Button size="sm" variant="ghost" leftIcon={<Icon name="play" size={14} />}>
                  播放
                </Button>
              </a>
              <Button
                size="sm"
                variant="ghost"
                loading={del.isPending}
                disabled={del.isPending}
                onClick={async () => {
                  if (await confirm({ title: '删除成片', message: '删除该集成片？磁盘上的成片文件将同步删除，不可恢复。', danger: true })) del.mutate(task.id)
                }}
                leftIcon={<Icon name="trash" size={14} />}
                className="!text-rose-500 hover:!text-rose-600"
              >
                删除
              </Button>
            </div>
          </div>
        </div>
      )}

      {!status && data?.task == null && !cover && (
        <p className="text-sm text-slate-400 mt-2">
          选择导出开关后点击「导出本集」：该幕幕级视频优先、无幕级视频时逐镜回退拼接为单集成片。
        </p>
      )}
    </div>
  )
}

export function ExportPanel({ projectId }: { projectId?: string }) {
  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get<Project[]>('/projects'),
  })

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(projectId ? [projectId] : []))
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(projectId ?? null)
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null)

  // 选中项目的幕列表（第 N 集顺序由后端按 index 排序返回）
  const { data: episodes = [] } = useQuery({
    queryKey: ['episodes', selectedProjectId],
    queryFn: () => api.get<Episode[]>(`/projects/${selectedProjectId}/episodes`),
    enabled: !!selectedProjectId,
  })

  const selectedEpisode = useMemo(
    () => episodes.find((e) => e.id === selectedEpisodeId) || null,
    [episodes, selectedEpisodeId],
  )

  // 进入页面 / 切换项目后未选幕时，自动选中第一幕
  useEffect(() => {
    if (!selectedEpisodeId && episodes.length > 0) {
      setSelectedEpisodeId(episodes[0].id)
    }
  }, [selectedEpisodeId, episodes])

  function toggleProject(id: string) {
    const willExpand = !expanded.has(id)
    setExpanded((prev) => {
      const next = new Set(prev)
      if (willExpand) next.add(id)
      else next.delete(id)
      return next
    })
    if (willExpand) {
      setSelectedProjectId(id)
      setSelectedEpisodeId(null)
    }
  }

  function selectEpisode(ep: Episode, projectIdOfEp: string) {
    setSelectedProjectId(projectIdOfEp)
    setSelectedEpisodeId(ep.id)
  }

  return (
    <div className="mt-8">
      <div className="flex items-center gap-2 mb-3">
        <Icon name="download" size={18} className="text-brand-600" />
        <h2 className="text-lg font-semibold text-slate-900">成片导出</h2>
        <span className="text-xs text-slate-400">项目成片（全片合集）+ 按幕导出单集成片</span>
      </div>

      {/* 项目成片（合集）：由项目详情页「合成完整视频」推送到此，作为项目成品 */}
      {selectedProjectId && <ProjectFilmExport projectId={selectedProjectId} />}

      <div className="flex gap-4 items-start">
        {/* 左侧导航栏：项目（顶级，可折叠）→ 幕（二级） */}
        <aside className="w-64 shrink-0 border border-slate-200 rounded-xl bg-white p-2 max-h-[560px] overflow-y-auto">
          <div className="px-2 py-1.5 text-xs font-medium text-slate-400">项目 / 剧集</div>
          {projects.length === 0 && <div className="px-2 py-3 text-xs text-slate-400">暂无项目</div>}
          {projects.map((p) => {
            const isExpanded = expanded.has(p.id)
            const isSelectedProject = selectedProjectId === p.id
            return (
              <div key={p.id} className="mb-1">
                <button
                  type="button"
                  onClick={() => toggleProject(p.id)}
                  className={cn(
                    'w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm text-left transition-colors',
                    isSelectedProject ? 'bg-brand-50 text-brand-700' : 'text-slate-700 hover:bg-slate-50',
                  )}
                >
                  <Icon name={isExpanded ? 'minus' : 'plus'} size={14} className="text-slate-400 shrink-0" />
                  <span className="truncate font-medium">{p.title}</span>
                </button>
                {isExpanded && isSelectedProject && (
                  <ul className="ml-3 mt-0.5 space-y-0.5 border-l border-slate-100 pl-2">
                    {episodes.length === 0 && (
                      <li className="px-2 py-1.5 text-xs text-slate-300">暂无幕</li>
                    )}
                    {episodes.map((ep) => (
                      <li key={ep.id}>
                        <button
                          type="button"
                          onClick={() => selectEpisode(ep, p.id)}
                          className={cn(
                            'w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-left transition-colors',
                            selectedEpisodeId === ep.id
                              ? 'bg-brand-100 text-brand-700 font-medium'
                              : 'text-slate-500 hover:bg-slate-50',
                          )}
                        >
                          <span className="shrink-0">第{ep.index + 1}集</span>
                          <span className="truncate">{ep.title || `第${ep.index + 1}集`}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </aside>

        {/* 右侧：选中幕的导出区（key 保证切换幕时重置状态） */}
        <div className="flex-1 min-w-0">
          {selectedEpisode ? (
            <EpisodeExportArea key={selectedEpisode.id} episode={selectedEpisode} />
          ) : (
            <div className="bg-white border border-slate-200 rounded-xl py-12 text-center text-sm text-slate-400">
              请选择左侧项目中的某一幕开始导出
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** 项目成片（全片合集）区：展示/预览/下载/重新合成项目详情页「合成完整视频」的产物。 */
function ProjectFilmExport({ projectId }: { projectId: string }) {
  const qc = useQueryClient()
  const toast = useToast()

  const { data: exports = [] } = useQuery({
    queryKey: ['project-exports', projectId],
    queryFn: () => api.get<Task[]>('/projects/' + projectId + '/exports'),
    // 任务活跃时轮询，驱动进度条
    refetchInterval: (q) =>
      q.state.data && q.state.data.some((t) => t.status === 'pending' || t.status === 'running') ? 2500 : false,
  })

  const latest = exports[0] ?? null   // 列表按 created_at 倒序，取最新一次合成
  const busy = !!latest && (latest.status === 'pending' || latest.status === 'running')

  const exportNow = useMutation({
    mutationFn: () => api.post<Task>('/projects/' + projectId + '/export'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project-exports', projectId] })
      toast.success('已开始合成项目成片')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const del = useMutation({
    mutationFn: (taskId: string) => api.del('/exports/' + taskId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project-exports', projectId] })
      toast.success('成片已删除')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const STATUS_TEXT: Record<TaskStatus, { text: string; variant: 'gray' | 'blue' | 'green' | 'red' }> = {
    pending: { text: '排队中', variant: 'gray' },
    running: { text: '合成中', variant: 'blue' },
    succeeded: { text: '已完成', variant: 'green' },
    failed: { text: '失败', variant: 'red' },
    cancelled: { text: '已取消', variant: 'gray' },
  }

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 mb-4">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-1.5">
            <Icon name="film" size={14} className="text-brand-600" /> 项目成片（全片合集）
          </h3>
          <p className="text-[11px] text-slate-400 mt-0.5">
            项目详情页「合成完整视频」生成的各分镜合集，作为本项目成品
          </p>
        </div>
        {latest && (
          <Badge variant={STATUS_TEXT[latest.status]?.variant ?? 'gray'} dot={busy}>
            {STATUS_TEXT[latest.status]?.text ?? latest.status}
          </Badge>
        )}
      </div>

      {busy ? (
        <ProgressBar value={latest?.progress ?? 0} variant="brand" label={'合成中 ' + (latest?.progress ?? 0) + '%'} />
      ) : latest?.status === 'succeeded' && latest.result_url ? (
        <div className="flex gap-4 items-start flex-wrap">
          <div className="w-80 shrink-0">
            <video src={latest.result_url} controls className="w-full rounded-lg border border-slate-200 bg-black max-h-56" />
          </div>
          <div className="text-xs text-slate-500 space-y-2 min-w-0">
            <p className="flex items-center gap-1.5">
              <Icon name="check-circle" size={13} className="text-emerald-500" />
              合成于 {latest.finished_at ? new Date(latest.finished_at).toLocaleString('zh-CN') : '—'}
            </p>
            <div className="flex items-center gap-2">
              <a
                href={latest.result_url}
                download
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-brand-500/40 text-brand-600 text-xs hover:bg-brand-500/10 transition-colors"
              >
                <Icon name="download" size={13} /> 下载成片
              </a>
              <Button
                variant="ghost"
                size="sm"
                className="!text-rose-500 hover:!text-rose-600"
                loading={del.isPending}
                onClick={() => { if (window.confirm('删除该成片？磁盘文件将同步删除，不可恢复。')) del.mutate(latest.id) }}
              >
                删除
              </Button>
            </div>
          </div>
        </div>
      ) : latest?.status === 'failed' ? (
        <p className="text-xs text-rose-600">{latest.error || '合成失败，可重试'}</p>
      ) : (
        <p className="text-xs text-slate-300">
          还未生成项目成片 —— 在项目详情页点击「合成完整视频」，完成后会自动出现在这里
        </p>
      )}

      <div className="mt-2.5">
        <Button variant="outline" size="sm" leftIcon={<Icon name="film" size={12} />} onClick={() => exportNow.mutate()} loading={exportNow.isPending}>
          合成项目成片
        </Button>
      </div>
    </div>
  )
}
