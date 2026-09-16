/**
 * 长期记忆管理对话框。
 */
import {useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {api} from '../../api/client'
import {Button} from '../ui/Button'
import {useToast} from '../ui/Toast'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import type {AgentMemory, AgentMemoryHit, Project} from '../../api/types'

export function MemoryDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [scope, setScope] = useState<'global' | 'project'>('global')
  const [pid, setPid] = useState('')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  /** P9 记忆语义检索关键词（非空时列表区展示命中结果） */
  const [searchQuery, setSearchQuery] = useState('')

  const { data: memories = [] } = useQuery<AgentMemory[]>({
    queryKey: ['agent-memories', scope, pid],
    queryFn: () =>
      api.get<AgentMemory[]>(
        scope === 'project'
          ? `/agent/memories?scope=project${pid ? `&project_id=${pid}` : ''}`
          : '/agent/memories?scope=global',
      ),
    enabled: open,
  })
  // P9 语义检索：jieba 分词 + TF-IDF 余弦，按相关度排序
  const { data: hits = [] } = useQuery<AgentMemoryHit[]>({
    queryKey: ['agent-memories-search', scope, pid, searchQuery.trim()],
    queryFn: () =>
      api.get<AgentMemoryHit[]>(
        `/agent/memories/search?q=${encodeURIComponent(searchQuery.trim())}${scope === 'project' ? `&scope=project${pid ? `&project_id=${pid}` : ''}` : '&scope=global'}&limit=10`,
      ),
    enabled: open && searchQuery.trim().length > 0,
  })
  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['agent-projects'],
    queryFn: () => api.get<Project[]>('/projects'),
    enabled: open,
  })

  if (!open) return null

  const add = async () => {
    if (!content.trim() || saving) return
    if (scope === 'project' && !pid) {
      toast.error('请先选择项目')
      return
    }
    setSaving(true)
    try {
      await api.post('/agent/memories', {
        content: content.trim(),
        scope,
        project_id: pid || null,
      })
      toast.success('记忆已保存')
      setContent('')
      qc.invalidateQueries({ queryKey: ['agent-memories'] })
    } catch (e) {
      toast.error((e as Error).message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (id: string) => {
    try {
      await api.del(`/agent/memories/${id}`)
      qc.invalidateQueries({ queryKey: ['agent-memories'] })
    } catch (e) {
      toast.error((e as Error).message || '删除失败')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[520px] max-h-[72vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="brain" size={14} className="text-brand-500" />
            记忆管理
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        {/* 范围切换 */}
        <div className="flex items-center gap-1.5 px-4 pt-3">
          {(['global', 'project'] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                setScope(s)
                setSearchQuery('')
              }}
              className={cn(
                'text-xs rounded-full px-3 py-1 border transition-colors',
                scope === s
                  ? 'bg-brand-500 text-white border-brand-500'
                  : 'border-slate-200 text-slate-600 hover:border-brand-300',
              )}
            >
              {s === 'global' ? '全局记忆' : '项目记忆'}
            </button>
          ))}
          {scope === 'project' && (
            <select
              value={pid}
              onChange={(e) => {
                setPid(e.target.value)
                setSearchQuery('')
              }}
              className="ml-auto input-base text-xs !py-1.5"
            >
              <option value="">选择项目…</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          )}
        </div>
        {/* P9 记忆语义检索 */}
        <div className="px-4 pt-2.5">
          <div className="relative">
            <Icon name="search" size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="语义搜索记忆（如：用户喜欢什么画风）…"
              className="w-full input-base text-xs !py-1.5 pl-7 pr-7"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 rounded text-slate-400 hover:text-slate-600"
                title="清空"
              >
                <Icon name="x" size={11} />
              </button>
            )}
          </div>
        </div>
        {/* 新增 */}
        <div className="flex items-center gap-2 px-4 py-3">
          <input
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return
              // 中文输入法组词确认（composition 期间回车）不触发
              if (e.nativeEvent.isComposing || e.keyCode === 229) return
              add()
            }}
            placeholder="添加记忆，如：用户偏好古风水墨风格"
            className="flex-1 input-base text-sm"
          />
          <Button size="sm" onClick={add} loading={saving} disabled={!content.trim()}>
            保存
          </Button>
        </div>
        {/* 列表：搜索态 → 语义命中；否则全部记忆 */}
        <div className="flex-1 overflow-y-auto px-4 pb-4 space-y-1.5">
          {searchQuery.trim() ? (
            hits.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-8">
                没有找到相关的记忆。可换个说法，或查看全部记忆。
              </p>
            ) : (
              hits.map((m) => (
                <div
                  key={m.id}
                  className="group flex items-start gap-2 rounded-xl border border-amber-200/70 bg-amber-50/40 px-3 py-2"
                >
                  <span className="shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full bg-amber-500" />
                  <p className="flex-1 text-xs text-slate-700 leading-relaxed">{m.content}</p>
                  <span
                    className={cn(
                      'shrink-0 text-[10px] font-medium rounded-full px-1.5 py-px',
                      m.score > 0.3 ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-400',
                    )}
                    title={`相关度 ${Math.round(m.score * 100)}%`}
                  >
                    {Math.round(m.score * 100)}%
                  </span>
                  <button
                    type="button"
                    onClick={() => remove(m.id)}
                    className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-slate-400 hover:text-rose-500"
                    title="删除记忆"
                  >
                    <Icon name="trash" size={12} />
                  </button>
                </div>
              ))
            )
          ) : memories.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-8">
              暂无记忆。可在对话中说「记住…」或「帮我记住…」让助手自动保存，也可在这里手动添加。
            </p>
          ) : (
            memories.map((m) => (
              <div
                key={m.id}
                className="group flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2"
              >
                <span className="shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full bg-brand-500" />
                <p className="flex-1 text-xs text-slate-700 leading-relaxed">{m.content}</p>
                <button
                  type="button"
                  onClick={() => remove(m.id)}
                  className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-slate-400 hover:text-rose-500"
                  title="删除记忆"
                >
                  <Icon name="trash" size={12} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

