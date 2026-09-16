/**
 * 批量任务锁链 hook（生成分镜 / 超分分镜共用）：
 * - 父任务 id 持久化到 sessionStorage（按 projectId+type 隔离）：切页/刷新返回后仍保持「进行中」
 * - 挂载时按 /projects/{id}/tasks 对账：找回仍在跑的同类批量任务（跨标签页/外部触发）；旧任务消失则释放
 * - useTaskPoller 每 2s 轮询父任务，pending/running 期间 busy=true（按钮转圈禁点），终态自动释放并刷新视频列表
 * - 对账只在任务列表（重新）拉取后执行，避免用旧缓存误清正在跑的任务
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import { useTaskPoller } from './useTaskPoller'
import type { Task } from './types'

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

export interface BatchOpOptions {
  /** 任务类型（后端 task.type），如 batch_videos / batch_upscale_videos */
  type: string
  /** sessionStorage key（应含 projectId），如 dsh.batchVideo.{projectId} */
  storageKey: string
  /** 挂载对账时接管到一个新任务（此前未跟踪）时回调，用于自动打开进度卡 */
  onAdopt?: () => void
  /** 任务进入终态（成功/失败/取消）时回调 */
  onSettled?: (task: Task) => void
}

export function useBatchOp(projectId: string, { type, storageKey, onAdopt, onSettled }: BatchOpOptions) {
  const qc = useQueryClient()
  const [taskId, setTaskId] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(storageKey)
    } catch {
      return null
    }
  })
  const mountedAt = useRef(Date.now())

  // 项目任务列表：① 切页/刷新返回后找回仍在跑的批量任务 ② 进度对账
  const { data: tasks = [], dataUpdatedAt } = useQuery({
    queryKey: ['tasks', projectId],
    queryFn: () => api.get<Task[]>('/projects/' + projectId + '/tasks'),
    refetchInterval: taskId ? 3000 : false,
  })

  // 对账：只在后端清单已（重新）拉取后执行，避免旧缓存误清正在跑的任务
  useEffect(() => {
    if (!tasks.length || dataUpdatedAt <= mountedAt.current) return
    const active = tasks.find(
      (t) => t.type === type && (t.status === 'pending' || t.status === 'running'),
    )
    if (active) {
      if (active.id !== taskId) {
        setTaskId(active.id)
        try {
          sessionStorage.setItem(storageKey, active.id)
        } catch {
          /* ignore */
        }
        // 仅接管新任务时触发 onAdopt（用户手动关过进度卡则不打扰）
        onAdopt?.()
      }
    } else if (taskId && !tasks.some((t) => t.id === taskId)) {
      // 后端已无该批量任务（被清理/级联删除）→ 释放锁定
      setTaskId(null)
      try {
        sessionStorage.removeItem(storageKey)
      } catch {
        /* ignore */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, dataUpdatedAt, taskId, type, storageKey])

  // 轮询：pending/running 期间 busy；终态自动释放
  const task = useTaskPoller(taskId)
  const busy = !!taskId && (!task || task.status === 'pending' || task.status === 'running')
  const progress = task?.progress ?? 0

  useEffect(() => {
    if (!task || !taskId) return
    if (TERMINAL.has(task.status)) {
      try {
        sessionStorage.removeItem(storageKey)
      } catch {
        /* ignore */
      }
      setTaskId(null)
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      qc.invalidateQueries({ queryKey: ['studio-clips'] })
      qc.invalidateQueries({ queryKey: ['segments'] })
      onSettled?.(task)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task, taskId])

  /** 派发成功后调用：立即锁定并持久化 */
  function begin(newTaskId: string) {
    if (!newTaskId) return
    setTaskId(newTaskId)
    try {
      sessionStorage.setItem(storageKey, newTaskId)
    } catch {
      /* ignore */
    }
  }

  return { taskId, busy, progress, task, begin }
}
