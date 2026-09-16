/**
 * MCP 服务器管理（对话框 + 编辑表单）。
 */
import {useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {api} from '../../api/client'
import {Button} from '../ui/Button'
import {useToast} from '../ui/Toast'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import type {AgentMcpServer, AgentMcpTestResult} from '../../api/types'

export function McpServerDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [editing, setEditing] = useState<AgentMcpServer | 'new' | null>(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, AgentMcpTestResult>>({})

  const { data: servers = [] } = useQuery<AgentMcpServer[]>({
    queryKey: ['agent-mcp-servers'],
    queryFn: () => api.get<AgentMcpServer[]>('/agent/mcp-servers'),
    enabled: open,
  })

  if (!open) return null

  const test = async (s: AgentMcpServer) => {
    setTesting(s.id)
    try {
      const res = await api.post<AgentMcpTestResult>(`/agent/mcp-servers/${s.id}/test`)
      setTestResult((prev) => ({ ...prev, [s.id]: res }))
      toast[res.ok ? 'success' : 'error'](res.message)
    } catch (e) {
      toast.error((e as Error).message || '连接失败')
    } finally {
      setTesting(null)
    }
  }

  const remove = async (id: string) => {
    await api.del(`/agent/mcp-servers/${id}`)
    toast.success('已删除 MCP 服务器')
    qc.invalidateQueries({ queryKey: ['agent-mcp-servers'] })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[760px] max-h-[76vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="package" size={14} className="text-brand-500" />
            MCP 服务器管理
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        {editing && (
          <McpServerEditForm
            server={editing === 'new' ? null : editing}
            onDone={() => {
              setEditing(null)
              qc.invalidateQueries({ queryKey: ['agent-mcp-servers'] })
            }}
            onCancel={() => setEditing(null)}
          />
        )}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[10px] font-medium text-slate-400">
              通过 stdio 本地进程接入 MCP 服务器，其工具以 mcp__&lt;server&gt;__&lt;tool&gt; 供模型调用
            </p>
            <Button size="sm" variant="outline" onClick={() => setEditing('new')} leftIcon={<Icon name="plus" size={12} />}>
              新建
            </Button>
          </div>
          {servers.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-6 border border-dashed border-slate-200 rounded-xl">
              暂无 MCP 服务器。可添加 stdio 本地进程（如 npx、uvx 启动的 MCP 服务）。
            </p>
          )}
          <div className="space-y-1.5">
            {servers.map((s) => {
              const tr = testResult[s.id]
              return (
                <div key={s.id} className="rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2">
                  <div className="flex items-center gap-2.5">
                    <span className="w-7 h-7 rounded-lg bg-cyan-50 text-cyan-600 flex items-center justify-center shrink-0">
                      <Icon name="package" size={13} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium text-slate-700 truncate">
                        {s.name}
                        <span className="ml-1.5 text-[10px] font-normal text-slate-400">
                          {s.enabled ? '启用中' : '已停用'}
                        </span>
                      </p>
                      <p className="text-[11px] text-slate-400 truncate">
                        {s.command} {s.args?.join(' ') ?? ''}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => test(s)}
                      disabled={testing === s.id}
                      className="text-[11px] font-medium text-brand-600 hover:underline shrink-0 flex items-center gap-1"
                    >
                      {testing === s.id ? (
                        <>
                          <Icon name="loader-2" size={11} className="animate-spin" /> 测试中…
                        </>
                      ) : (
                        <>
                          <Icon name="refresh" size={11} /> 连接测试
                        </>
                      )}
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
                  {tr && (
                    <div className="mt-2 pl-9.5">
                      <p
                        className={cn(
                          'text-[11px] mb-1',
                          tr.ok ? 'text-emerald-600' : 'text-rose-500',
                        )}
                      >
                        {tr.message}
                      </p>
                      {tr.tools.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {tr.tools.map((t) => (
                            <span
                              key={t.function.name}
                              className="text-[10px] rounded-full bg-cyan-50 text-cyan-700 border border-cyan-100 px-2 py-0.5"
                            >
                              {t.function.name}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

export function McpServerEditForm({
  server,
  onDone,
  onCancel,
}: {
  server: AgentMcpServer | null
  onDone: () => void
  onCancel: () => void
}) {
  const toast = useToast()
  const [name, setName] = useState(server?.name ?? '')
  const [description, setDescription] = useState(server?.description ?? '')
  const [command, setCommand] = useState(server?.command ?? '')
  const [argsText, setArgsText] = useState(server?.args?.join(' ') ?? '')
  const [envText, setEnvText] = useState(
    Object.entries(server?.env ?? {}).map(([k, v]) => `${k}=${v}`).join('\n'),
  )
  const [enabled, setEnabled] = useState(server?.enabled ?? true)
  const [sort, setSort] = useState(server?.sort ?? 0)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (!name.trim() || !command.trim() || saving) return
    setSaving(true)
    try {
      const args = argsText
        .split(/\s+/)
        .map((s) => s.trim())
        .filter(Boolean)
      const env: Record<string, string> = {}
      for (const line of envText.split('\n')) {
        const t = line.trim()
        if (!t) continue
        const eq = t.indexOf('=')
        if (eq > 0) env[t.slice(0, eq).trim()] = t.slice(eq + 1).trim()
      }
      const body = {
        name: name.trim(),
        description: description.trim(),
        command: command.trim(),
        args,
        env: Object.keys(env).length ? env : null,
        enabled,
        sort,
      }
      if (server) {
        await api.put(`/agent/mcp-servers/${server.id}`, body)
      } else {
        await api.post('/agent/mcp-servers', body)
      }
      toast.success(server ? '已更新 MCP 服务器' : '已创建 MCP 服务器')
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
        <Icon name={server ? 'pencil' : 'plus'} size={12} className="text-brand-500" />
        {server ? '编辑 MCP 服务器' : '新建 MCP 服务器'}
      </div>
      <div className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!!server}
          placeholder="服务器名（如 filesystem）"
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
      <input
        value={command}
        onChange={(e) => setCommand(e.target.value)}
        placeholder="启动命令（如 npx、uvx、python）"
        className="w-full input-base text-sm"
      />
      <input
        value={argsText}
        onChange={(e) => setArgsText(e.target.value)}
        placeholder="启动参数（空格分隔，如 -y @modelcontextprotocol/server-filesystem /tmp）"
        className="w-full input-base text-sm"
      />
      <input
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="描述（可选）"
        className="w-full input-base text-sm"
      />
      <textarea
        value={envText}
        onChange={(e) => setEnvText(e.target.value)}
        rows={2}
        placeholder="环境变量（每行 KEY=VALUE，可选）"
        className="w-full input-base text-sm resize-none"
      />
      <div className="flex justify-end gap-2">
        <label className="flex items-center gap-1.5 text-xs text-slate-500 mr-auto cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="accent-brand-500"
          />
          启用
        </label>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" onClick={save} loading={saving} disabled={!name.trim() || !command.trim()}>
          保存
        </Button>
      </div>
    </div>
  )
}

