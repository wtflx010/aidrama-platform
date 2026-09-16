/**
 * 定时自动化任务管理对话框。
 */
import {useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {api} from '../../api/client'
import {Button} from '../ui/Button'
import {useToast} from '../ui/Toast'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import type {AgentSchedule} from '../../api/types'

export function ScheduleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [cronExpr, setCronExpr] = useState('0 9 * * *')
  const [actionType, setActionType] = useState<'prompt' | 'system'>('prompt')
  const [prompt, setPrompt] = useState('')
  const [saving, setSaving] = useState(false)
  const [runningId, setRunningId] = useState<string | null>(null)

  const { data: schedules = [] } = useQuery<AgentSchedule[]>({
    queryKey: ['agent-schedules'],
    queryFn: () => api.get<AgentSchedule[]>('/agent/schedules'),
    enabled: open,
  })

  if (!open) return null

  const create = async () => {
    if (!name.trim() || !cronExpr.trim() || saving) return
    if (actionType === 'prompt' && !prompt.trim()) {
      toast.error('请填写 prompt（交给 LLM 的生成指令）')
      return
    }
    setSaving(true)
    try {
      await api.post('/agent/schedules', {
        name: name.trim(),
        cron_expr: cronExpr.trim(),
        action_type: actionType,
        prompt: actionType === 'prompt' ? prompt.trim() : '',
        system_action: actionType === 'system' ? 'retry_failed_tasks' : '',
      })
      toast.success('定时任务已创建')
      setName('')
      setPrompt('')
      setShowForm(false)
      qc.invalidateQueries({ queryKey: ['agent-schedules'] })
    } catch (e) {
      toast.error((e as Error).message || '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (s: AgentSchedule) => {
    try {
      await api.put(`/agent/schedules/${s.id}`, { enabled: !s.enabled })
      qc.invalidateQueries({ queryKey: ['agent-schedules'] })
    } catch (e) {
      toast.error((e as Error).message || '操作失败')
    }
  }

  const remove = async (id: string) => {
    try {
      await api.del(`/agent/schedules/${id}`)
      toast.success('已删除定时任务')
      qc.invalidateQueries({ queryKey: ['agent-schedules'] })
    } catch (e) {
      toast.error((e as Error).message || '删除失败')
    }
  }

  const runNow = async (s: AgentSchedule) => {
    setRunningId(s.id)
    try {
      const r = await api.post<{ message: string }>(`/agent/schedules/${s.id}/run`)
      toast.success(r.message || '已执行')
      qc.invalidateQueries({ queryKey: ['agent-schedules'] })
    } catch (e) {
      toast.error((e as Error).message || '执行失败')
    } finally {
      setRunningId(null)
    }
  }

  const fmtTime = (t: string | null) =>
    t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '—'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[680px] max-h-[76vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="clock" size={14} className="text-brand-500" />
            定时自动化
            <span className="text-[11px] font-normal text-slate-400">
              cron 调度 · beat 每 30s 扫描 · 生成类任务默认不自动触发
            </span>
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        {/* 新建入口 + 表单 */}
        <div className="px-4 pt-3">
          <Button size="sm" variant="outline" onClick={() => setShowForm((v) => !v)} leftIcon={<Icon name="plus" size={12} />}>
            {showForm ? '收起表单' : '新建定时任务'}
          </Button>
        </div>
        {showForm && (
          <div className="px-4 py-3 space-y-2 border-b border-slate-100 bg-slate-50/60">
            <div className="flex gap-2">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="任务名称（如：每日进度报告）"
                className="flex-1 input-base text-sm"
              />
              <input
                value={cronExpr}
                onChange={(e) => setCronExpr(e.target.value)}
                placeholder="cron 表达式"
                className="w-40 input-base text-sm font-mono"
              />
            </div>
            <div className="flex gap-1.5">
              {(['prompt', 'system'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setActionType(t)}
                  className={cn(
                    'text-xs rounded-lg px-2.5 py-1 border transition-colors',
                    actionType === t
                      ? 'bg-brand-500 text-white border-brand-500'
                      : 'border-slate-200 text-slate-600 hover:border-brand-300',
                  )}
                >
                  {t === 'prompt' ? '定时对话/报告' : '系统动作'}
                </button>
              ))}
              <span className="ml-auto text-[11px] text-slate-400 self-center">
                例：0 9 * * *（每天9点）· */1 * * * *（每分钟，测试用）
              </span>
            </div>
            {actionType === 'prompt' ? (
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={2}
                placeholder="交给 LLM 的生成指令（如：请总结当前所有项目的最新进度，生成一份简报）。产出会写入自动创建的会话"
                className="w-full input-base text-sm resize-none"
              />
            ) : (
              <p className="text-xs text-slate-500">
                系统动作：<code className="font-mono text-brand-600">retry_failed_tasks</code>
                （批量重跑所有失败任务，不触发生成类新建）
              </p>
            )}
            <div className="flex justify-end">
              <Button size="sm" onClick={create} loading={saving} disabled={!name.trim() || !cronExpr.trim()}>
                创建
              </Button>
            </div>
          </div>
        )}
        {/* 列表 */}
        <div className="flex-1 overflow-y-auto p-4 space-y-1.5">
          {schedules.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-8">
              暂无定时任务。可创建每日报告、定时生成简报等自动化任务。
            </p>
          )}
          {schedules.map((s) => (
            <div key={s.id} className="rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2">
              <div className="flex items-center gap-2.5">
                <span
                  className={cn(
                    'shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full',
                    s.enabled ? 'bg-emerald-500' : 'bg-slate-300',
                  )}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium text-slate-700 truncate">
                    {s.name}
                    <span className="ml-1.5 text-[10px] font-normal text-slate-400">
                      {s.action_type === 'prompt' ? '定时对话' : '系统动作'}
                      {!s.enabled && ' · 已停用'}
                    </span>
                  </p>
                  <p className="text-[11px] text-slate-400 truncate">
                    <code className="font-mono">{s.cron_expr}</code>
                    <span className="mx-1">·</span>
                    下次 {fmtTime(s.next_run_at)}
                    {s.run_count > 0 && (
                      <>
                        <span className="mx-1">·</span>已运行 {s.run_count} 次
                      </>
                    )}
                  </p>
                  {s.last_error && <p className="text-[11px] text-rose-500 truncate">{s.last_error}</p>}
                </div>
                <div className="flex items-center gap-0.5 shrink-0">
                  <button
                    type="button"
                    onClick={() => runNow(s)}
                    disabled={runningId === s.id || !s.enabled}
                    className="p-1 rounded text-slate-400 hover:text-brand-600 disabled:opacity-40"
                    title="立即运行一次"
                  >
                    {runningId === s.id ? (
                      <Icon name="loader-2" size={12} className="animate-spin" />
                    ) : (
                      <Icon name="play" size={12} />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => toggle(s)}
                    className="p-1 rounded text-slate-400 hover:text-brand-600"
                    title={s.enabled ? '停用' : '启用'}
                  >
                    <Icon name="power" size={12} />
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(s.id)}
                    className="p-1 rounded text-slate-400 hover:text-rose-500"
                    title="删除"
                  >
                    <Icon name="trash" size={12} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

