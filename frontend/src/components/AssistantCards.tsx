/**
 * 进度轮询卡片（2026-08-14 从 Assistant.tsx 拆分，减小超大组件体积）。
 * 长篇小说写作 / 分集剧本写作 / AI 视频草稿：各自轮询对应任务状态。
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Task, VideoDraft } from '../api/types'
import { ProgressBar } from './ui/ProgressBar'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

export function NovelWriteCard({
  writing,
}: {
  writing: { novelId: string; taskId: string; title: string; chapters: number }
}) {
  const [task, setTask] = useState<Task | null>(null)

  useEffect(() => {
    if (!writing.taskId) return
    let cancelled = false
    let timer: ReturnType<typeof setInterval> | undefined
    const poll = async () => {
      try {
        const t = await api.get<Task>(`/tasks/${writing.taskId}`)
        if (cancelled) return
        setTask(t)
        if (t.status === 'succeeded' || t.status === 'failed') clearInterval(timer)
      } catch {
        /* 网络抖动忽略 */
      }
    }
    poll()
    timer = setInterval(poll, 4000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [writing.taskId])

  const done = task?.status === 'succeeded'
  const failed = task?.status === 'failed'
  const progress = task?.progress ?? 0
  const written = Math.min(writing.chapters, Math.max(0, Math.ceil((progress / 100) * writing.chapters)))

  return (
    <div className="max-w-[480px]">
      <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-100 bg-slate-50/70">
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full',
              failed ? 'bg-rose-500' : done ? 'bg-emerald-500' : 'bg-brand-500 animate-pulse',
            )}
          />
          <span className="text-xs font-medium text-slate-600 truncate">长篇写作 ·《{writing.title}》</span>
          {!done && !failed && (
            <span className="text-[10px] text-slate-400 ml-auto shrink-0">{progress}%</span>
          )}
        </div>
        <div className="p-3">
          {failed ? (
            <p className="text-xs text-rose-500">{task?.error || '写作失败，请重试'}</p>
          ) : done ? (
            <div className="space-y-2">
              <p className="text-xs text-emerald-600 flex items-center gap-1.5">
                <Icon name="check-circle" size={13} />
                已完成 {writing.chapters} 章，正文已入库
              </p>
              <Link
                to="/novels"
                className="flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:underline"
              >
                <Icon name="book" size={13} />
                前往剧本库「分析 → 生成项目」
              </Link>
            </div>
          ) : (
            <div className="space-y-2">
              <ProgressBar value={progress} variant="brand" />
              <p className="text-xs text-slate-400 flex items-center gap-1.5">
                <Icon name="loader-2" size={13} className="animate-spin text-brand-500" />
                后台逐章写作中… 已写 {written}/{writing.chapters} 章
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── 分集剧本写作轮询卡片 ─────────────────────────────



export function ScriptWriteCard({
  writing,
}: {
  writing: { taskId: string; novelId: string; title: string; episodes: number }
}) {
  const [task, setTask] = useState<Task | null>(null)
  const [prog, setProg] = useState<{ script_task?: Task | null; shot_task?: Task | null; poster_task?: Task | null }>({})

  useEffect(() => {
    if (!writing.taskId) return
    let cancelled = false
    let timer: ReturnType<typeof setInterval> | undefined
    const poll = async () => {
      try {
        const t = await api.get<Task>('/tasks/' + writing.taskId)
        if (cancelled) return
        setTask(t)
        if (t.status === 'succeeded' || t.status === 'failed') clearInterval(timer)
      } catch {
        /* 网络抖动忽略 */
      }
    }
    poll()
    timer = setInterval(poll, 4000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [writing.taskId])

  // 分镜预览 / 剧本海报 进度（写剧本完成后自动生成，随 writing 完成后一并展示）
  useEffect(() => {
    if (!writing.novelId) return
    let cancelled = false
    let timer: ReturnType<typeof setInterval> | undefined
    const poll = async () => {
      try {
        const d = await api.get<{ script_task: Task | null; shot_task: Task | null; poster_task: Task | null }>(
          '/novels/' + writing.novelId + '/progress',
        )
        if (cancelled) return
        setProg(d)
      } catch {
        /* 忽略 */
      }
    }
    poll()
    timer = setInterval(poll, 4000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [writing.novelId])

  const scriptDone = task?.status === 'succeeded'
  const failed = task?.status === 'failed'
  const shotSt = prog.shot_task?.status ?? null
  const posterSt = prog.poster_task?.status ?? null
  const progress = task?.progress ?? 0
  const written = Math.min(writing.episodes, Math.max(0, Math.ceil((progress / 100) * writing.episodes)))
  const overallDone = scriptDone && (shotSt === 'succeeded') && (posterSt === 'succeeded')

  function StageRow({ label, st, pct }: { label: string; st?: string | null; pct?: number | null }) {
    const ok = st === 'succeeded'
    const err = st === 'failed'
    const run = st === 'running' || st === 'pending'
    return (
      <div className="flex items-center gap-2 text-xs min-w-0">
        {ok ? (
          <Icon name="check-circle" size={13} className="text-emerald-500 shrink-0" />
        ) : err ? (
          <Icon name="alert-circle" size={13} className="text-rose-500 shrink-0" />
        ) : (
          <Icon name="loader-2" size={13} className="animate-spin text-brand-500 shrink-0" />
        )}
        <span className="text-slate-600 truncate">{label}</span>
        {ok && <span className="text-emerald-500 ml-auto shrink-0">完成</span>}
        {err && <span className="text-rose-500 ml-auto shrink-0">失败</span>}
        {run && <span className="text-slate-400 ml-auto shrink-0">{pct ?? 0}%</span>}
        {!st && <span className="text-slate-300 ml-auto shrink-0">等待</span>}
      </div>
    )
  }

  return (
    <div className="max-w-[480px]">
      <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-100 bg-slate-50/70">
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full',
              failed ? 'bg-rose-500' : overallDone ? 'bg-emerald-500' : 'bg-brand-500 animate-pulse',
            )}
          />
          <span className="text-xs font-medium text-slate-600 truncate">剧本创作 ·《{writing.title}》</span>
          {!overallDone && !failed && (
            <span className="text-[10px] text-slate-400 ml-auto shrink-0">{progress}%</span>
          )}
        </div>
        <div className="p-3">
          {failed ? (
            <p className="text-xs text-rose-500">{task?.error || '剧本写作失败，请重试'}</p>
          ) : overallDone ? (
            <div className="space-y-2">
              <p className="text-xs text-emerald-600 flex items-center gap-1.5">
                <Icon name="check-circle" size={13} />
                《{writing.title}》已完成：剧本 · 分镜预览 · 海报 均已生成
              </p>
              <Link
                to={writing.novelId ? '/novels/' + writing.novelId : '/novels'}
                className="flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:underline"
              >
                <Icon name="book" size={13} />
                前往剧本库确认后生成项目
              </Link>
            </div>
          ) : (
            <div className="space-y-2">
              <StageRow
                label={'剧本 · 后台逐集写作（' + written + '/' + writing.episodes + ' 集）'}
                st={task?.status ?? null}
                pct={progress}
              />
              {/* 四阶段确认流：分镜/海报只在对应阶段任务实际创建后展示（确认大纲→写剧本→确认剧本→分镜→确认→海报+项目） */}
              {shotSt !== null && (
                <StageRow
                  label="分镜 · 按已确认剧本生成（MiniMax H3 规范）"
                  st={shotSt}
                  pct={prog.shot_task?.progress ?? null}
                />
              )}
              {posterSt !== null && (
                <StageRow
                  label="剧本海报 · 后台生成中"
                  st={posterSt}
                  pct={prog.poster_task?.progress ?? null}
                />
              )}
              <div className="flex items-center gap-3 pt-1 text-[10px] text-slate-300 font-mono">
                {writing.novelId && <span>剧本文档 ID {writing.novelId.slice(0, 8)}…</span>}
                {writing.taskId && <span>写作任务 ID {writing.taskId.slice(0, 8)}…</span>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
// ─── 视频草稿轮询卡片 ─────────────────────────────────



export function VideoDraftCard({ draftId }: { draftId: string }) {
  const [draft, setDraft] = useState<VideoDraft | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!draftId) return
    let cancelled = false
    let timer: ReturnType<typeof setInterval> | undefined
    const poll = async () => {
      try {
        const d = await api.get<VideoDraft>(`/video-drafts/${draftId}`)
        if (cancelled) return
        setDraft(d)
        if (d.status === 'succeeded' || d.status === 'failed') {
          clearInterval(timer)
          if (d.status === 'failed') setError(d.error || '生成失败')
        }
      } catch {
        /* 网络抖动忽略 */
      }
    }
    poll()
    timer = setInterval(poll, 3000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId])

  if (error) return <div className="text-xs text-rose-500">{error}</div>
  if (draft?.video_url) {
    return <video src={draft.video_url} controls className="w-full rounded-lg" />
  }
  return (
    <div className="space-y-2">
      {draft && draft.status === 'running' && <ProgressBar value={40} variant="blue" />}
      <p className="text-xs text-slate-400 flex items-center gap-1.5">
        <Icon name="video" size={13} className="animate-pulse" />
        视频生成中（约 1~3 分钟）…
      </p>
    </div>
  )
}

