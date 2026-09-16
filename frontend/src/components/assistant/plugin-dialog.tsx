/**
 * 生成插件管理（选择器 + 对话框 + 编辑表单）。
 */
import {useState, useEffect, useRef} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {api} from '../../api/client'
import {Button} from '../ui/Button'
import {useToast} from '../ui/Toast'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import {PLUGIN_ICONS} from './shared'
import type {AgentPlugin} from '../../api/types'

export function PluginSelector({
  plugins,
  value,
  onChange,
  onManage,
  disabled,
}: {
  plugins: AgentPlugin[]
  value: string[]
  onChange: (v: string[]) => void
  onManage: () => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const enabled = plugins.filter((p) => p.enabled)
  const selected = enabled.filter((p) => value.includes(p.name))
  // 插件单选：选中即收起对话框；重复点击已选中的插件 = 取消选择
  const toggle = (name: string) => {
    onChange(name === value[0] ? [] : [name])
    setOpen(false)
  }

  return (
    <div className="relative" ref={ref}>
      {/* 已选插件标签 + 选择按钮 */}
      <div className="flex items-center gap-1 flex-wrap max-w-[220px]">
        {selected.map((p) => (
          <span
            key={p.name}
            className="inline-flex items-center gap-1 text-[11px] font-medium rounded-full bg-amber-50 border border-amber-200 text-amber-700 px-2 py-1"
          >
            <Icon name={PLUGIN_ICONS[p.name] ?? 'zap'} size={10} />
            {p.label}
            <button
              type="button"
              onClick={() => toggle(p.name)}
              className="hover:text-rose-500"
              title="取消插件"
            >
              <Icon name="x" size={10} />
            </button>
          </span>
        ))}
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          className={cn(
            'flex items-center gap-1.5 text-xs font-medium rounded-lg px-2 py-1 transition-colors',
            selected.length > 0
              ? 'text-amber-700 bg-amber-50 hover:bg-amber-100'
              : 'text-slate-500 hover:bg-slate-100 hover:text-brand-600',
            open && 'bg-slate-100 text-brand-600',
            disabled && 'opacity-50 cursor-not-allowed',
          )}
        >
          <Icon name="zap" size={13} />
          插件
          {selected.length > 0 && <span className="text-[10px] font-bold">{selected.length}</span>}
          <Icon name="chevron-down" size={12} className="opacity-60" />
        </button>
      </div>
      {open && (
        <div className="absolute bottom-full left-0 mb-1.5 w-72 rounded-xl border border-slate-200 bg-white shadow-lg py-1 z-30">
          <div className="px-3 py-1.5 border-b border-slate-100 flex items-center justify-between">
            <span className="text-[11px] font-medium text-slate-500">选择生成插件（单选，选中即收起）</span>
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                onManage()
              }}
              className="text-[11px] font-medium text-brand-600 hover:underline"
            >
              管理插件
            </button>
          </div>
          {enabled.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-4">暂无可用插件</p>
          )}
          {enabled.map((p) => (
            <button
              key={p.name}
              type="button"
              onClick={() => toggle(p.name)}
              className={cn(
                'w-full flex items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-slate-50',
                value.includes(p.name) && 'bg-amber-50/60 hover:bg-amber-50',
              )}
            >
              <span
                className={cn(
                  'w-7 h-7 rounded-lg flex items-center justify-center shrink-0',
                  value.includes(p.name) ? 'bg-amber-100 text-amber-600' : 'bg-slate-100 text-slate-500',
                )}
              >
                <Icon name={PLUGIN_ICONS[p.name] ?? 'zap'} size={13} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium text-slate-700">
                  {p.label}
                  {p.is_builtin && <span className="ml-1 text-[10px] font-normal text-slate-400">内置</span>}
                </span>
                <span className="block text-[11px] text-slate-400">{p.description}</span>
              </span>
              {value.includes(p.name) && <Icon name="check" size={14} className="text-amber-500 shrink-0 mt-1" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 生成插件管理弹层（内置 + 可管理，内容变更同步更新保障可用）──────

const PLUGIN_MODE_LABELS: Record<string, string> = {
  t2i: '文生图', i2i: '图生图', t2v: '文生视频', i2v: '图生视频', multi_ref: '多图参考生视频',
}

export function PluginDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [editing, setEditing] = useState<AgentPlugin | 'new' | null>(null)

  const { data: plugins = [] } = useQuery<AgentPlugin[]>({
    queryKey: ['agent-plugins'],
    queryFn: () => api.get<AgentPlugin[]>('/agent/plugins'),
    enabled: open,
  })

  if (!open) return null

  const toggle = async (p: AgentPlugin) => {
    try {
      await api.put(`/agent/plugins/${p.id}`, { enabled: !p.enabled })
      toast.success(p.enabled ? '已停用' : '已启用')
      qc.invalidateQueries({ queryKey: ['agent-plugins'] })
    } catch (e) {
      toast.error((e as Error).message || '操作失败')
    }
  }

  const remove = async (id: string) => {
    await api.del(`/agent/plugins/${id}`)
    toast.success('已删除插件')
    qc.invalidateQueries({ queryKey: ['agent-plugins'] })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[720px] max-h-[76vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="zap" size={14} className="text-amber-500" />
            生成插件管理
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        {editing && (
          <PluginEditForm
            plugin={editing === 'new' ? null : editing}
            onDone={() => {
              setEditing(null)
              qc.invalidateQueries({ queryKey: ['agent-plugins'] })
            }}
            onCancel={() => setEditing(null)}
          />
        )}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[10px] font-medium text-slate-400">
              对话前在输入框选择插件 → 描述需求 → 执行。内置插件修改后自动同步，保障可用
            </p>
            <Button size="sm" variant="outline" onClick={() => setEditing('new')} leftIcon={<Icon name="plus" size={12} />}>
              新建
            </Button>
          </div>
          <div className="space-y-1.5">
            {plugins.map((p) => (
              <div
                key={p.id}
                className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2"
              >
                <span className="w-7 h-7 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center shrink-0">
                  <Icon name={PLUGIN_ICONS[p.name] ?? 'zap'} size={13} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium text-slate-700 truncate">
                    {p.label}
                    <span className="ml-1.5 text-[10px] font-normal text-slate-400">
                      {p.name} · {PLUGIN_MODE_LABELS[p.mode] ?? p.mode} · {p.tool}
                      {p.is_builtin ? ' · 内置' : ''}
                    </span>
                  </p>
                  <p className="text-[11px] text-slate-400 truncate">{p.description}</p>
                </div>
                <button
                  type="button"
                  onClick={() => toggle(p)}
                  className={cn(
                    'text-[11px] font-medium rounded-full px-2 py-0.5 border transition-colors shrink-0',
                    p.enabled
                      ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                      : 'bg-slate-100 text-slate-400 border-slate-200',
                  )}
                >
                  {p.enabled ? '启用中' : '已停用'}
                </button>
                <button
                  type="button"
                  onClick={() => setEditing(p)}
                  className="p-1 rounded text-slate-400 hover:text-brand-600 hover:bg-slate-100"
                  title="编辑"
                >
                  <Icon name="pencil" size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => remove(p.id)}
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

export function PluginEditForm({
  plugin,
  onDone,
  onCancel,
}: {
  plugin: AgentPlugin | null
  onDone: () => void
  onCancel: () => void
}) {
  const toast = useToast()
  const [name, setName] = useState(plugin?.name ?? '')
  const [label, setLabel] = useState(plugin?.label ?? '')
  const [description, setDescription] = useState(plugin?.description ?? '')
  const [prompt, setPrompt] = useState(plugin?.prompt ?? '')
  const [tool, setTool] = useState(plugin?.tool ?? 'generate_image')
  const [mode, setMode] = useState(plugin?.mode ?? 't2i')
  const [enabled, setEnabled] = useState(plugin?.enabled ?? true)
  const [sort, setSort] = useState(plugin?.sort ?? 0)
  const [saving, setSaving] = useState(false)

  // 内置插件：工具/模式按代码同步，仅可编辑 label/description/prompt/启用
  const isBuiltin = plugin?.is_builtin ?? false

  const save = async () => {
    if (!name.trim() || !label.trim() || !prompt.trim() || saving) return
    setSaving(true)
    try {
      const body = {
        name: name.trim(),
        label: label.trim(),
        description: description.trim(),
        prompt: prompt.trim(),
        tool,
        mode,
        enabled,
        sort,
      }
      if (plugin) {
        await api.put(`/agent/plugins/${plugin.id}`, body)
      } else {
        await api.post('/agent/plugins', body)
      }
      toast.success(plugin ? '已更新插件' : '已创建插件')
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
        <Icon name={plugin ? 'pencil' : 'plus'} size={12} className="text-amber-500" />
        {plugin ? '编辑插件' : '新建插件'}
        {isBuiltin && <span className="text-[10px] font-normal text-slate-400 ml-1">内置插件：工具/模式由系统同步，修改 prompt 后自动生效</span>}
      </div>
      <div className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!!plugin}
          placeholder="插件标识（如 t2i、multi_ref）"
          className="flex-1 input-base text-sm"
        />
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="显示名（如 文生图）"
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
      <div className="flex gap-1.5">
        <select
          value={tool}
          onChange={(e) => setTool(e.target.value)}
          disabled={isBuiltin}
          className="input-base text-sm"
        >
          <option value="generate_image">generate_image（生图）</option>
          <option value="generate_video">generate_video（生视频）</option>
          <option value="web_search">web_search（联网搜索）</option>
        </select>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          disabled={isBuiltin}
          className="input-base text-sm"
        >
          {Object.entries(PLUGIN_MODE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-slate-500 ml-auto cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="accent-amber-500"
          />
          启用
        </label>
      </div>
      <input
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="插件简介（选择器中展示）"
        className="w-full input-base text-sm"
      />
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={4}
        placeholder="选中插件后注入模型的执行约束（模型据此调用工具与传参）。如「已启用「文生图」插件。当用户描述画面需求时，调用 generate_image 工具…」"
        className="w-full input-base text-sm resize-none"
      />
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" onClick={save} loading={saving} disabled={!name.trim() || !label.trim() || !prompt.trim()}>
          保存
        </Button>
      </div>
    </div>
  )
}

// ─── 规则管理弹层（对齐 TraeWork Rules：全局/项目级规则自动注入对话）──


