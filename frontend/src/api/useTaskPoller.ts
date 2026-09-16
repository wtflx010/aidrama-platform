/**
 * 任务轮询 hook（P3 实时性增强）：
 * - 每 2s 拉取任务状态，pending/running 时持续，终态停止
 * - 卡住检测：基于 last_heartbeat_at / updated_at，超过 STALE_THRESHOLD 未刷新 → isStale
 * - 网络错误计数：连续失败超过 MAX_ERRORS → networkError 上报
 * - 返回 task + 派生状态（isStale / waitSeconds / networkError）
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from './client'
import type { Task } from './types'

const POLL_INTERVAL = 2000
const ERROR_INTERVAL = 4000
const MAX_ERRORS = 3 // 连续 3 次网络失败 → 上报
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

/** 心跳超过此秒数未刷新 → 判定疑似卡住。
 * 2026-08-09（P2-3）：与后端 reclaim 回收阈值（STUCK_THRESHOLD=5 分钟）对齐，
 * 避免任务正常生成（视频链路较长）时前端过早提示「疑似卡住」造成误导。 */
const STALE_THRESHOLD_SEC = 300

export interface TaskPollState {
  task: Task | null
  /** 是否疑似卡住（running 且心跳超时） */
  isStale: boolean
  /** 自 started_at 起的已等待秒数（running/pending 时计算） */
  waitSeconds: number
  /** 连续网络错误次数（>0 表示轮询有问题） */
  networkErrors: number
  /** 网络错误是否已达上报阈值 */
  networkError: boolean
}

export function useTaskPoller(taskId: string | null | undefined): Task | null
export function useTaskPoller(taskId: string | null | undefined, opts: { detailed: true }): TaskPollState
export function useTaskPoller(
  taskId: string | null | undefined,
  opts?: { detailed?: true },
): Task | null | TaskPollState {
  const [task, setTask] = useState<Task | null>(null)
  const [networkErrors, setNetworkErrors] = useState(0)
  // tick 用于定期重算 waitSeconds/isStale（即使 task 对象不变）
  const [, setTick] = useState(0)

  useEffect(() => {
    if (!taskId) {
      setTask(null)
      setNetworkErrors(0)
      return
    }
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const poll = async () => {
      try {
        const t = await api.get<Task>(`/tasks/${taskId}`)
        if (cancelled) return
        setTask(t)
        setNetworkErrors(0)
        if (TERMINAL.has(t.status)) return // 终态，停止轮询
        timer = setTimeout(poll, POLL_INTERVAL)
      } catch {
        if (cancelled) return
        setNetworkErrors((n) => n + 1)
        // 网络抖动等，退避后重试（不停止，避免后端短暂抖动丢失状态）
        timer = setTimeout(poll, ERROR_INTERVAL)
      }
    }
    poll()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [taskId])

  // 定期 tick 以刷新派生状态（仅在有活跃任务时）
  useEffect(() => {
    if (!task || TERMINAL.has(task.status)) return
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [task?.status])

  const derived = useMemo<TaskPollState>(() => {
    const nowSec = Date.now() / 1000
    const startSec = task?.started_at ? new Date(task.started_at).getTime() / 1000 : null
    const waitSeconds = startSec && !TERMINAL.has(task!.status) ? Math.max(0, Math.floor(nowSec - startSec)) : 0

    // 心跳时间：优先 last_heartbeat_at，其次 updated_at
    const hbStr = task?.last_heartbeat_at || task?.updated_at
    const hbSec = hbStr ? new Date(hbStr).getTime() / 1000 : null
    const isStale =
      !!task &&
      task.status === 'running' &&
      !!hbSec &&
      nowSec - hbSec > STALE_THRESHOLD_SEC

    return {
      task,
      isStale,
      waitSeconds,
      networkErrors,
      networkError: networkErrors >= MAX_ERRORS,
    }
  }, [task, networkErrors, /* tick 触发重算 */ task?.status])

  // taskId 为空时同步返回空态：不要等 effect 异步 setTask(null)。
  // 否则任务结束后 task 会残留旧快照，删除成片等场景下 observed 仍引用已删除任务，
  // 导致 UI 一直显示旧状态（刷新后才恢复）。
  if (!taskId) {
    if (opts?.detailed) {
      return { task: null, isStale: false, waitSeconds: 0, networkErrors: 0, networkError: false }
    }
    return null
  }
  if (opts?.detailed) return derived
  return task
}

/** 格式化已等待秒数为 "1m23s" / "45s" / "2m" 形式。 */
export function formatWait(sec: number): string {
  if (sec <= 0) return ''
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m === 0) return `${s}s`
  if (s === 0) return `${m}m`
  return `${m}m${s}s`
}
