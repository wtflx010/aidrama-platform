/**
 * 全局任务中心（P3 实时性）：
 * - 右上角悬浮按钮：带进行中任务数量徽章，点击展开右侧抽屉
 * - 抽屉列出项目所有任务（按 created_at desc），轮询刷新
 * - 每个任务卡片：类型图标 + 标签 + 状态徽章 + 进度条 + 已等待时长 + 卡住提示 + 取消按钮
 * - 跨分镜/Tab 可见，解决"切走丢轮询"痛点
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { formatWait } from '../api/useTaskPoller'
import type { Task, TaskType, TaskStatus } from '../api/types'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'
import { ProgressBar } from './ui/ProgressBar'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

const POLL_MS = 3000

type BadgeVariant = 'gray' | 'blue' | 'green' | 'red' | 'purple' | 'orange' | 'indigo' | 'amber'

const STATUS_BADGE: Record<TaskStatus, { text: string; variant: BadgeVariant }> = {
  pending: { text: '排队中', variant: 'gray' },
  running: { text: '生成中', variant: 'blue' },
  succeeded: { text: '已完成', variant: 'green' },
  failed: { text: '失败', variant: 'red' },
  cancelled: { text: '已取消', variant: 'gray' },
}

const TYPE_META: Partial<Record<TaskType, { icon: string; label: string }>> = {
  generate_keyframe: { icon: 'film', label: '关键帧' },
  generate_video: { icon: 'video', label: '视频' },
  generate_voice: { icon: 'mic', label: '配音' },
  // generate_asset_cover / generate_asset_fourview 的标签按 target_type 动态生成
  // （character/scene/prop），见 resolveAssetLabel
  generate_asset_cover: { icon: 'image', label: '封面图' },
  generate_asset_fourview: { icon: 'grid', label: '四视图' },
  generate_scene_multiview: { icon: 'image', label: '场景多视角' },
  batch_keyframes: { icon: 'film', label: '批量关键帧' },
  batch_videos: { icon: 'video', label: '批量视频' },
  batch_voice: { icon: 'mic', label: '批量配音' },
  batch_asset_covers: { icon: 'image', label: '批量封面' },
  export_film: { icon: 'download', label: '成片导出' },
  export_episode: { icon: 'download', label: '剧集导出' },
  analyze_novel: { icon: 'book', label: '剧本分析' },
  generate_shot_plan: { icon: 'film', label: '生成分镜' },
  adapt_script: { icon: 'folder', label: '生成项目' },
  adapt_continuation: { icon: 'book', label: '章节追加' },
  generate_bgm: { icon: 'music', label: 'BGM' },
  generate_sfx: { icon: 'headphones', label: '音效' },
  generate_episode_design: { icon: 'image', label: '幕首/尾图' },
  generate_episode_video: { icon: 'video', label: '幕级视频' },
  project_director: { icon: 'film', label: '连续长片' },
  write_script: { icon: 'book', label: '剧本写作' },
  write_novel: { icon: 'book', label: '小说写作' },
  generate_video_draft: { icon: 'video', label: 'AI视频' },
  generate_action_sequence_template: { icon: 'grid', label: '白模模板图' },
  compose_action_sequence: { icon: 'film', label: '动作序列合成' },
  upscale_video: { icon: 'wand', label: '视频超分' },
  batch_upscale_videos: { icon: 'wand', label: '批量超分' },
}

// 资产任务按 target_type 区分角色/场景/道具
const ASSET_TYPE_LABEL: Record<string, string> = {
  character: '角色',
  scene: '场景',
  prop: '道具',
}

const ASSET_TYPE_ICON: Record<string, string> = {
  character: 'users',
  scene: 'image',
  prop: 'package',
}

/** 解析资产类任务的标签和图标（区分角色/场景/道具） */
function resolveAssetMeta(task: Task): { icon: string; label: string } {
  const base = TYPE_META[task.type] || { icon: 'zap', label: task.type }
  const typeLabel = ASSET_TYPE_LABEL[task.target_type]
  if (!typeLabel) return base
  const icon = ASSET_TYPE_ICON[task.target_type] || base.icon
  // 四视图仅角色支持，标签不重复加"角色"
  if (task.type === 'generate_asset_fourview') {
    return { icon, label: '角色四视图' }
  }
  return { icon, label: `${typeLabel}${base.label}` }
}

const STALE_SEC = 90

function isPending(s: TaskStatus) {
  return s === 'pending' || s === 'running'
}

function isStale(t: Task): boolean {
  if (t.status !== 'running') return false
  const hbStr = t.last_heartbeat_at || t.updated_at
  if (!hbStr) return false
  return Date.now() / 1000 - new Date(hbStr).getTime() / 1000 > STALE_SEC
}

function waitSec(t: Task): number {
  if (!t.started_at || !isPending(t.status)) return 0
  return Math.max(0, Math.floor(Date.now() / 1000 - new Date(t.started_at).getTime() / 1000))
}

/** 执行日期格式化：MM-DD HH:mm（跨年显示年份）。执行时间优先取 started_at，排队中回退创建时间。 */
function fmtExecTime(s: string | null): string {
  if (!s) return '--'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return '--'
  const pad = (n: number) => String(n).padStart(2, '0')
  const sameYear = d.getFullYear() === new Date().getFullYear()
  return `${sameYear ? '' : d.getFullYear() + '-'}${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function TaskCenter({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()

  // 轮询项目所有任务（进行中优先，但也展示最近完成的）
  const { data: tasks = [] } = useQuery({
    queryKey: ['tasks', projectId],
    queryFn: () => api.get<Task[]>(`/projects/${projectId}/tasks`),
    refetchInterval: POLL_MS,
  })

  const activeCount = tasks.filter((t) => isPending(t.status)).length
  const staleCount = tasks.filter((t) => isStale(t)).length
  const failedCount = tasks.filter((t) => t.status === 'failed').length

  const cancelMut = useMutation({
    mutationFn: (taskId: string) => api.post<Task>(`/tasks/${taskId}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks', projectId] }),
  })

  // 批量重跑失败任务（2026-08-09）
  const retryMut = useMutation({
    mutationFn: () => api.post<{ count: number; message: string }>(`/projects/${projectId}/tasks/retry-failed`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      qc.invalidateQueries({ queryKey: ['videos'] })
      qc.invalidateQueries({ queryKey: ['keyframes'] })
      qc.invalidateQueries({ queryKey: ['voice'] })
    },
  })

  return (
    <>
      {/* 悬浮按钮 */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="fixed top-4 right-4 z-40 w-11 h-11 rounded-full bg-white border border-slate-200 shadow-lg flex items-center justify-center hover:shadow-xl hover:border-brand-300 transition-all"
        title="任务中心"
      >
        <Icon name="clock" size={20} className="text-slate-600" />
        {activeCount > 0 && (
          <span className="absolute -top-1 -right-1 min-w-5 h-5 px-1 rounded-full bg-brand-500 text-white text-[10px] font-semibold flex items-center justify-center">
            {activeCount}
          </span>
        )}
        {staleCount > 0 && activeCount === 0 && (
          <span className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-amber-500 border-2 border-white" />
        )}
      </button>

      {/* 抽屉 */}
      {open && createPortal(
        <div className="fixed inset-0 z-50">
          {/* 遮罩 */}
          <div
            className="absolute inset-0 bg-black/20 backdrop-blur-sm animate-fade-in"
            onClick={() => setOpen(false)}
          />
          {/* 抽屉面板 */}
          <div className="absolute top-0 right-0 h-full w-[420px] max-w-[90vw] bg-white shadow-2xl flex flex-col animate-slide-in-right">
            {/* 头部 */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
              <div className="flex items-center gap-2">
                <Icon name="clock" size={18} className="text-brand-600" />
                <h2 className="text-base font-semibold text-slate-900">任务中心</h2>
                <span className="text-xs text-slate-400">{tasks.length} 个任务</span>
                {staleCount > 0 && (
                  <Badge variant="amber" size="sm">{staleCount} 疑似卡住</Badge>
                )}
                {failedCount > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    loading={retryMut.isPending}
                    onClick={() => {
                      if (window.confirm(`重跑 ${failedCount} 个失败任务？`)) retryMut.mutate()
                    }}
                    leftIcon={<Icon name="refresh" size={12} />}
                  >
                    重跑失败 ({failedCount})
                  </Button>
                )}
              </div>
              <button
                onClick={() => setOpen(false)}
                className="p-1.5 rounded-lg text-slate-500 hover:text-slate-700 hover:bg-slate-100"
                aria-label="关闭"
              >
                <Icon name="x" size={18} />
              </button>
            </div>

            {/* 任务列表 */}
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              {tasks.length === 0 ? (
                <div className="text-center text-sm text-slate-400 py-12">
                  <Icon name="check" size={32} className="mx-auto mb-2 text-slate-300" />
                  暂无任务
                </div>
              ) : (
                tasks.map((t) => (
                  <TaskCard
                    key={t.id}
                    task={t}
                    onCancel={cancelMut.mutate}
                    canceling={cancelMut.isPending}
                  />
                ))
              )}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}

function TaskCard({
  task,
  onCancel,
  canceling,
}: {
  task: Task
  onCancel: (id: string) => void
  canceling: boolean
}) {
  // 资产类任务按 target_type 动态区分角色/场景/道具
  const isAssetTask = task.type === 'generate_asset_cover' || task.type === 'generate_asset_fourview'
  const meta = isAssetTask ? resolveAssetMeta(task) : (TYPE_META[task.type] || { icon: 'zap', label: task.type })
  const badge = STATUS_BADGE[task.status]
  const stale = isStale(task)
  const wait = waitSec(task)
  const pending = isPending(task.status)

  return (
    <div className={cn(
      'bg-slate-50 border rounded-lg p-3',
      stale ? 'border-amber-300 bg-amber-50/40' : 'border-slate-200',
    )}>
      <div className="flex items-center gap-2 mb-1.5">
        <Icon name={meta.icon} size={14} className="text-slate-500 shrink-0" />
        <span className="text-sm font-medium text-slate-800">{meta.label}</span>
        <Badge variant={badge.variant} size="sm" dot={pending}>{badge.text}</Badge>
        <span className="ml-auto flex items-center gap-2 text-[11px] text-slate-400 shrink-0">
          {wait > 0 && (
            <span className="flex items-center gap-0.5" title="已等待">
              <Icon name="clock" size={10} />
              {formatWait(wait)}
            </span>
          )}
          <span
            className="flex items-center gap-0.5"
            title={task.started_at ? '开始执行时间' : '创建（排队）时间'}
          >
            <Icon name="calendar" size={10} />
            {fmtExecTime(task.started_at ?? task.created_at)}
          </span>
        </span>
      </div>

      {/* 目标定位：分镜级任务显示「分镜 m-n · 标题」，其余显示目标 id（缩短） */}
      <p className="text-[10px] text-slate-400 mb-1.5 font-mono truncate">
        {task.segment_ref &&
        (task.segment_ref.episode_index != null || task.segment_ref.segment_index != null) ? (
          <>
            <Icon name="map-pin" size={10} className="inline mr-0.5 text-slate-300" />
            <span className="font-semibold text-slate-500">
              分镜 {task.segment_ref.episode_index ?? '?'}-{task.segment_ref.segment_index ?? '?'}
            </span>
            {task.segment_ref.title && (
              <span className="text-slate-400"> · {task.segment_ref.title}</span>
            )}
          </>
        ) : (
          <>{task.target_type} · {task.target_id.slice(0, 8)}</>
        )}
        {task.segment_ref?.description && (
          <span className="text-slate-300"> · {task.segment_ref.description}</span>
        )}
      </p>

      {/* 进度条（pending/running 才显示） */}
      {pending && (
        <ProgressBar
          value={task.progress}
          variant={stale ? 'blue' : 'brand'}
          label={task.status === 'running' ? `${task.progress}%` : '排队中…'}
        />
      )}

      {/* 卡住提示 */}
      {stale && (
        <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-amber-700 bg-amber-100/60 border border-amber-200 rounded px-2 py-1">
          <Icon name="alert-circle" size={11} />
          疑似卡住（{formatWait(wait)}无心跳更新）
        </div>
      )}

      {/* 错误信息 */}
      {task.status === 'failed' && task.error && (
        <div className="mt-1.5 text-[11px] text-rose-600 bg-rose-50 border border-rose-200 rounded px-2 py-1 break-all">
          {task.error}
        </div>
      )}

      {/* 操作：取消（仅 pending/running） */}
      {pending && (
        <div className="mt-2 flex justify-end">
          <Button
            size="sm"
            variant="ghost"
            loading={canceling}
            onClick={() => onCancel(task.id)}
            className="text-rose-500 hover:text-rose-600"
            leftIcon={<Icon name="x" size={12} />}
          >
            取消
          </Button>
        </div>
      )}
    </div>
  )
}
