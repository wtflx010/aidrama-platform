/**
 * 规则管理（对话框 + 编辑表单）。
 */
import {useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {api} from '../../api/client'
import {Button} from '../ui/Button'
import {useToast} from '../ui/Toast'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import type {AgentRule, Project} from '../../api/types'

export function RuleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [editing, setEditing] = useState<AgentRule | null>(null)
  const [showForm, setShowForm] = useState(false)

  const { data: rules = [] } = useQuery<AgentRule[]>({
    queryKey: ['agent-rules'],
    queryFn: () => api.get<AgentRule[]>('/agent/rules'),
    enabled: open,
  })

  if (!open) return null

  const toggle = async (r: AgentRule) => {
    try {
      await api.put(`/agent/rules/${r.id}`, { enabled: !r.enabled })
      qc.invalidateQueries({ queryKey: ['agent-rules'] })
    } catch (e) {
      toast.error((e as Error).message || '操作失败')
    }
  }

  const remove = async (id: string) => {
    try {
      await api.del(`/agent/rules/${id}`)
      qc.invalidateQueries({ queryKey: ['agent-rules'] })
    } catch (e) {
      toast.error((e as Error).message || '删除失败')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[560px] max-h-[76vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="list" size={14} className="text-brand-500" />
            规则管理
            <span className="text-[11px] font-normal text-slate-400">全局 + 项目级，自动注入对话约束智能体行为</span>
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-1.5">
          {rules.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-8">
              暂无规则。可添加如「默认画幅 9:16」「回答使用简体中文」等全局约束，助手每次对话都会遵守。
            </p>
          )}
          {rules.map((r) => (
            <div
              key={r.id}
              className="group flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2"
            >
              <span
                className={cn(
                  'shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full',
                  r.enabled ? 'bg-emerald-500' : 'bg-slate-300',
                )}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-medium text-slate-700">{r.name}</span>
                  <span className="text-[10px] rounded-full px-1.5 py-px bg-slate-200 text-slate-500">
                    {r.scope === 'global' ? '全局' : '项目'}
                  </span>
                  {!r.enabled && <span className="text-[10px] text-slate-400">已停用</span>}
                </div>
                <p className="text-xs text-slate-600 leading-relaxed mt-0.5">{r.content}</p>
              </div>
              <div className="flex items-center gap-0.5 shrink-0">
                <button
                  type="button"
                  onClick={() => {
                    setEditing(r)
                    setShowForm(true)
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded text-slate-400 hover:text-brand-600"
                  title="编辑"
                >
                  <Icon name="pencil" size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => toggle(r)}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded text-slate-400 hover:text-brand-600"
                  title={r.enabled ? '停用' : '启用'}
                >
                  <Icon name="power" size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => remove(r.id)}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded text-slate-400 hover:text-rose-500"
                  title="删除"
                >
                  <Icon name="trash" size={12} />
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="px-4 py-3 border-t border-slate-100">
          <Button size="sm" className="w-full" onClick={() => { setEditing(null); setShowForm(true) }}>
            新建规则
          </Button>
        </div>
        {showForm && (
          <RuleEditForm
            rule={editing}
            onDone={() => {
              setShowForm(false)
              setEditing(null)
              qc.invalidateQueries({ queryKey: ['agent-rules'] })
            }}
            onCancel={() => {
              setShowForm(false)
              setEditing(null)
            }}
          />
        )}
      </div>
    </div>
  )
}

export function RuleEditForm({
  rule,
  onDone,
  onCancel,
}: {
  rule: AgentRule | null
  onDone: () => void
  onCancel: () => void
}) {
  const toast = useToast()
  const [name, setName] = useState(rule?.name ?? '')
  const [content, setContent] = useState(rule?.content ?? '')
  const [scope, setScope] = useState<'global' | 'project'>(rule?.scope ?? 'global')
  const [projectId, setProjectId] = useState(rule?.project_id ?? '')
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [sort] = useState(rule?.sort ?? 0)
  const [saving, setSaving] = useState(false)

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['agent-projects'],
    queryFn: () => api.get<Project[]>('/projects'),
    enabled: scope === 'project',
  })

  const save = async () => {
    if (!name.trim() || !content.trim() || saving) return
    if (scope === 'project' && !projectId) {
      toast.error('项目级规则请先选择项目')
      return
    }
    setSaving(true)
    try {
      const body = {
        name: name.trim(),
        content: content.trim(),
        scope,
        project_id: scope === 'project' ? projectId : null,
        enabled,
        sort,
      }
      if (rule) {
        await api.put(`/agent/rules/${rule.id}`, body)
      } else {
        await api.post('/agent/rules', body)
      }
      toast.success('规则已保存')
      onDone()
    } catch (e) {
      toast.error((e as Error).message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border-t border-slate-100 px-4 py-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="规则名称（如：默认画幅 9:16）"
          className="flex-1 input-base text-sm"
        />
        <select value={scope} onChange={(e) => setScope(e.target.value as 'global' | 'project')} className="input-base text-xs !py-2">
          <option value="global">全局</option>
          <option value="project">项目级</option>
        </select>
      </div>
      {scope === 'project' && (
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="w-full input-base text-sm">
          <option value="">选择项目…</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title}
            </option>
          ))}
        </select>
      )}
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="规则内容（注入对话的约束文本），如：生成图片默认使用 9:16 竖版画幅；回答保持简体中文；禁止编造工具未返回的事实。"
        rows={3}
        className="w-full input-base text-sm resize-none"
      />
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-1.5 text-xs text-slate-500">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          启用
        </label>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel}>
            取消
          </Button>
          <Button size="sm" onClick={save} loading={saving} disabled={!name.trim() || !content.trim()}>
            保存
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── P8 定时自动化任务弹层（cron 调度，对齐 Hermes/OpenClaw）──────


