/**
 * Skill 管理（对话框 + 编辑表单）。
 */
import {useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {api} from '../../api/client'
import {Button} from '../ui/Button'
import {useToast} from '../ui/Toast'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import {SKILL_TYPE_LABELS} from './shared'
import type {AgentSkill} from '../../api/types'

export function SkillDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [editing, setEditing] = useState<AgentSkill | 'new' | null>(null)

  const { data: skills = [] } = useQuery<AgentSkill[]>({
    queryKey: ['agent-skills'],
    queryFn: () => api.get<AgentSkill[]>('/agent/skills'),
    enabled: open,
  })

  if (!open) return null

  const toggle = async (s: AgentSkill) => {
    try {
      await api.put(`/agent/skills/${s.id}`, { enabled: !s.enabled })
      toast.success(s.enabled ? '已停用' : '已启用')
      qc.invalidateQueries({ queryKey: ['agent-skills'] })
    } catch (e) {
      toast.error((e as Error).message || '操作失败')
    }
  }

  const remove = async (id: string) => {
    await api.del(`/agent/skills/${id}`)
    toast.success('已删除 Skill')
    qc.invalidateQueries({ queryKey: ['agent-skills'] })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[720px] max-h-[76vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="zap" size={14} className="text-brand-500" />
            Skill 管理
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        {editing && (
          <SkillEditForm
            skill={editing === 'new' ? null : editing}
            onDone={() => {
              setEditing(null)
              qc.invalidateQueries({ queryKey: ['agent-skills'] })
            }}
            onCancel={() => setEditing(null)}
          />
        )}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[10px] font-medium text-slate-400">
              prompt 型注册为 skill_&lt;name&gt; 工具由模型调用；knowledge 型仅注入对话上下文；builtin_tool 映射内置工具
            </p>
            <Button size="sm" variant="outline" onClick={() => setEditing('new')} leftIcon={<Icon name="plus" size={12} />}>
              新建
            </Button>
          </div>
          {skills.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-6 border border-dashed border-slate-200 rounded-xl">
              暂无 Skill。可创建可调用技能（模型按需调用）、知识注入或内置工具映射。
            </p>
          )}
          <div className="space-y-1.5">
            {skills.map((s) => (
              <div
                key={s.id}
                className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2"
              >
                <span className="w-7 h-7 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center shrink-0">
                  <Icon name="zap" size={13} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium text-slate-700 truncate">
                    {s.name}
                    <span className="ml-1.5 text-[10px] font-normal text-slate-400">
                      {SKILL_TYPE_LABELS[s.tool_type] ?? s.tool_type}
                      {s.handler ? ` → ${s.handler}` : ''}
                      {s.is_builtin ? ' · 内置' : ''}
                    </span>
                  </p>
                  <p className="text-[11px] text-slate-400 truncate">
                    {s.description || s.prompt.slice(0, 60)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => toggle(s)}
                  className={cn(
                    'text-[11px] font-medium rounded-full px-2 py-0.5 border transition-colors shrink-0',
                    s.enabled
                      ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                      : 'bg-slate-100 text-slate-400 border-slate-200',
                  )}
                >
                  {s.enabled ? '启用中' : '已停用'}
                </button>
                <button
                  type="button"
                  onClick={() => setEditing(s)}
                  className="p-1 rounded text-slate-400 hover:text-brand-600 hover:bg-slate-100"
                  title="编辑"
                >
                  <Icon name="pencil" size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => remove(s.id)}
                  className="p-1 rounded text-slate-400 hover:text-rose-500 hover:bg-slate-100"
                  title="删除"
                >
                  <Icon name="trash" size={12} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export function SkillEditForm({
  skill,
  onDone,
  onCancel,
}: {
  skill: AgentSkill | null
  onDone: () => void
  onCancel: () => void
}) {
  const toast = useToast()
  const [name, setName] = useState(skill?.name ?? '')
  const [description, setDescription] = useState(skill?.description ?? '')
  const [prompt, setPrompt] = useState(skill?.prompt ?? '')
  const [toolType, setToolType] = useState(skill?.tool_type ?? 'prompt')
  const [handler, setHandler] = useState(skill?.handler ?? '')
  const [enabled, setEnabled] = useState(skill?.enabled ?? true)
  const [sort, setSort] = useState(skill?.sort ?? 0)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (!name.trim() || !description.trim() || !prompt.trim() || saving) return
    setSaving(true)
    try {
      const body = {
        name: name.trim(),
        description: description.trim(),
        prompt: prompt.trim(),
        tool_type: toolType,
        handler: handler.trim() || null,
        enabled,
        sort,
      }
      if (skill) {
        await api.put(`/agent/skills/${skill.id}`, body)
      } else {
        await api.post('/agent/skills', body)
      }
      toast.success(skill ? '已更新 Skill' : '已创建 Skill')
      onDone()
    } catch (e) {
      toast.error((e as Error).message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border-b border-slate-100 bg-slate-50/60 px-4 py-3 space-y-2.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-700">
        <Icon name={skill ? 'pencil' : 'plus'} size={12} className="text-brand-500" />
        {skill ? '编辑 Skill' : '新建 Skill'}
      </div>
      <div className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!!skill}
          placeholder="Skill 名称（如 story_brainstorm）"
          className="flex-1 input-base text-sm"
        />
        <input
          value={sort}
          onChange={(e) => setSort(Number(e.target.value) || 0)}
          type="number"
          placeholder="排序"
          className="w-20 input-base text-sm"
        />
      </div>
      <div className="flex items-center gap-2">
        <div className="flex gap-1.5">
          {(['prompt', 'knowledge', 'builtin_tool'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setToolType(t)}
              className={cn(
                'text-xs rounded-lg px-2.5 py-1 border transition-colors',
                toolType === t
                  ? 'bg-brand-500 text-white border-brand-500'
                  : 'border-slate-200 text-slate-600 hover:border-brand-300',
              )}
            >
              {SKILL_TYPE_LABELS[t]}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-xs text-slate-500 ml-auto cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="accent-brand-500"
          />
          启用
        </label>
      </div>
      <input
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="一句话描述（模型据其决定是否调用 / 注入说明）"
        className="w-full input-base text-sm"
      />
      <input
        value={handler}
        onChange={(e) => setHandler(e.target.value)}
        placeholder="handler（builtin_tool 时填内置工具名，如 web_search）"
        className="w-full input-base text-sm"
      />
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={4}
        placeholder={
          toolType === 'prompt'
            ? '可调用技能提示词：模型调用 skill_<name> 时按此独立执行，直接产出成果。如「你是创意策划，根据用户主题产出 3 个带反转的剧情点子…」'
            : '知识注入内容：随对话上下文注入，帮助模型理解该领域。'
        }
        className="w-full input-base text-sm resize-none"
      />
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" onClick={save} loading={saving} disabled={!name.trim() || !description.trim() || !prompt.trim()}>
          保存
        </Button>
      </div>
    </div>
  )
}

// ─── MCP 服务器管理弹层（stdio 本地进程，2026-08-11）──────


