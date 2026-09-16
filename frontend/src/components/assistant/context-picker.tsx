/**
 * @ 上下文引用选择器 + 斜杠命令菜单。
 */
import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {api} from '../../api/client'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import {ASSET_TYPE_LABELS, CTX_TABS} from './shared'
import type {ContextTab} from './shared'
import type {AgentSession, Asset, ContextRef, Novel, Project} from '../../api/types'

export function ContextPickerDialog({
  open,
  onClose,
  onSelect,
}: {
  open: boolean
  onClose: () => void
  onSelect: (ref: ContextRef) => void
}) {
  const [tab, setTab] = useState<ContextTab>('project')
  const [pid, setPid] = useState('')
  const [keyword, setKeyword] = useState('')

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['agent-projects'],
    queryFn: () => api.get<Project[]>('/projects'),
    enabled: open,
  })
  const { data: novels = [] } = useQuery<Novel[]>({
    queryKey: ['agent-novels'],
    queryFn: () => api.get<Novel[]>('/novels'),
    enabled: open,
  })
  const { data: sessions = [] } = useQuery<AgentSession[]>({
    queryKey: ['agent-sessions'],
    queryFn: () => api.get<AgentSession[]>('/agent/sessions'),
    enabled: open,
  })
  const { data: assets = [] } = useQuery<Asset[]>({
    queryKey: ['agent-project-assets', pid],
    queryFn: () => api.get<Asset[]>(`/projects/${pid}/assets`),
    enabled: open && tab === 'asset' && !!pid,
  })

  if (!open) return null

  const kw = keyword.trim().toLowerCase()
  const match = (label: string) => !kw || label.toLowerCase().includes(kw)
  const pick = (ref: ContextRef) => {
    onSelect(ref)
    setKeyword('')
  }
  const noResult = (text: string) => <p className="text-center text-xs text-slate-400 py-8">{text}</p>

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[540px] max-h-[72vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="at-sign" size={14} className="text-brand-500" />
            引用上下文
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        {/* Tab 切换 + 关键词搜索 */}
        <div className="flex items-center gap-1.5 px-4 pt-3 pb-2">
          {CTX_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={cn(
                'flex items-center gap-1 text-xs rounded-full px-3 py-1 border transition-colors',
                tab === t.key
                  ? 'bg-brand-500 text-white border-brand-500'
                  : 'border-slate-200 text-slate-600 hover:border-brand-300 hover:text-brand-600',
              )}
            >
              <Icon name={t.icon} size={11} />
              {t.label}
            </button>
          ))}
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索…"
            className="ml-auto w-28 input-base text-xs !py-1.5"
          />
        </div>
        {/* 资产模式：先选项目 */}
        {tab === 'asset' && (
          <div className="px-4 py-2.5 border-b border-slate-50 max-h-20 overflow-y-auto flex flex-wrap gap-1.5">
            {projects.length === 0 && <p className="text-xs text-slate-400 py-1">暂无项目</p>}
            {projects.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setPid(p.id)}
                className={cn(
                  'text-xs rounded-full px-2.5 py-1 border transition-colors',
                  pid === p.id
                    ? 'bg-brand-500 text-white border-brand-500'
                    : 'border-slate-200 text-slate-600 hover:border-brand-300 hover:text-brand-600',
                )}
              >
                {p.title}
              </button>
            ))}
          </div>
        )}
        {/* 列表区域 */}
        <div className="flex-1 overflow-y-auto p-3 space-y-1">
          {tab === 'project' &&
            (projects.filter((p) => match(p.title)).length === 0
              ? noResult('暂无匹配项目')
              : projects
                  .filter((p) => match(p.title))
                  .map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => pick({ type: 'project', id: p.id, label: p.title })}
                      className="w-full flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2 text-left hover:border-brand-300 hover:bg-brand-50/50 transition-colors"
                    >
                      <span className="w-8 h-8 rounded-lg bg-white border border-slate-200 flex items-center justify-center text-brand-500 shrink-0">
                        <Icon name="folder" size={15} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium text-slate-700 truncate">{p.title}</span>
                        <span className="block text-[11px] text-slate-400">项目 · {p.status}</span>
                      </span>
                      <Icon name="chevron-right" size={13} className="ml-auto text-slate-300" />
                    </button>
                  )))}
          {tab === 'document' &&
            (novels.filter((n) => match(n.title)).length === 0
              ? noResult('暂无匹配剧本文档')
              : novels
                  .filter((n) => match(n.title))
                  .map((n) => (
                    <button
                      key={n.id}
                      type="button"
                      onClick={() => pick({ type: 'document', id: n.id, label: n.title })}
                      className="w-full flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2 text-left hover:border-brand-300 hover:bg-brand-50/50 transition-colors"
                    >
                      <span className="w-8 h-8 rounded-lg bg-white border border-slate-200 flex items-center justify-center text-brand-500 shrink-0">
                        <Icon name="book" size={15} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium text-slate-700 truncate">{n.title}</span>
                        <span className="block text-[11px] text-slate-400">剧本文档 · {n.chapters_count} 章</span>
                      </span>
                      <Icon name="chevron-right" size={13} className="ml-auto text-slate-300" />
                    </button>
                  )))}
          {tab === 'session' &&
            (sessions.filter((s) => match(s.title)).length === 0
              ? noResult('暂无匹配历史会话')
              : sessions
                  .filter((s) => match(s.title))
                  .map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => pick({ type: 'session', id: s.id, label: s.title })}
                      className="w-full flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2 text-left hover:border-brand-300 hover:bg-brand-50/50 transition-colors"
                    >
                      <span className="w-8 h-8 rounded-lg bg-white border border-slate-200 flex items-center justify-center text-brand-500 shrink-0">
                        <Icon name="clapperboard" size={15} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium text-slate-700 truncate">{s.title}</span>
                        <span className="block text-[11px] text-slate-400">历史会话</span>
                      </span>
                      <Icon name="chevron-right" size={13} className="ml-auto text-slate-300" />
                    </button>
                  )))}
          {tab === 'asset' &&
            (!pid
              ? noResult('先选择项目，再选择要引用的资产')
              : assets.filter((a) => match(a.name)).length === 0
                ? noResult('该项目暂无匹配资产（角色/场景/道具）')
                : (
                    <div className="grid grid-cols-3 gap-2">
                      {assets
                        .filter((a) => match(a.name))
                        .map((a) => (
                          <button
                            key={a.id}
                            type="button"
                            onClick={() => pick({ type: 'asset', id: a.id, label: a.name })}
                            className="rounded-xl border border-slate-200 overflow-hidden hover:border-brand-400 hover:shadow-md transition-all text-left group"
                          >
                            {a.cover_url ? (
                              <img
                                src={a.cover_url}
                                alt={a.name}
                                className="w-full h-24 object-cover group-hover:scale-[1.03] transition-transform"
                              />
                            ) : (
                              <div className="w-full h-24 flex items-center justify-center bg-slate-100 text-slate-300">
                                <Icon name="image" size={20} />
                              </div>
                            )}
                            <div className="px-1.5 py-1">
                              <span className="text-[10px] text-brand-500 font-medium">
                                {ASSET_TYPE_LABELS[a.type] ?? a.type}
                              </span>
                              <p className="text-xs text-slate-700 truncate">{a.name}</p>
                            </div>
                          </button>
                        ))}
                    </div>
                  ))}
        </div>
      </div>
    </div>
  )
}

// ─── 斜杠命令菜单（/plan 创作规划、/goal 创作目标，Trae Work 风格）────────

const SLASH_COMMANDS = [
  {
    key: 'plan',
    title: '创作规划',
    desc: '把需求拆解为规划文档，确认后分步执行',
    template: '/plan ',
    icon: 'list',
  },
  {
    key: 'goal',
    title: '创作目标',
    desc: '设定目标，AI 多轮自动推进并自评是否达成',
    template: '/goal ',
    icon: 'target',
  },
  {
    key: 'novel',
    title: '长篇小说',
    desc: '先生成大纲，确认后后台逐章写作入库，可改编剧本',
    template: '/novel ',
    icon: 'book',
  },
] as const

export function SlashCommandMenu({
  onPick,
}: {
  onPick: (cmd: { key: string; template: string; title: string }) => void
  onClose: () => void
}) {
  return (
    <div className="absolute bottom-full left-2 mb-1 w-72 rounded-xl border border-slate-200 bg-white shadow-lg py-1 z-30">
      <p className="px-3 py-1.5 text-[10px] font-medium text-slate-400">斜杠命令</p>
      {SLASH_COMMANDS.map((c) => (
        <button
          key={c.key}
          type="button"
          onClick={() => onPick(c)}
          className="w-full flex items-start gap-2.5 px-3 py-2 text-left hover:bg-slate-50 transition-colors"
        >
          <span className="shrink-0 mt-0.5 w-7 h-7 rounded-lg bg-brand-50 text-brand-600 flex items-center justify-center">
            <Icon name={c.icon} size={14} />
          </span>
          <span className="min-w-0">
            <span className="block text-[13px] font-medium text-slate-700">
              <span className="text-brand-600 font-semibold">/{c.key}</span> {c.title}
            </span>
            <span className="block text-[11px] text-slate-400 mt-0.5 leading-relaxed">{c.desc}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

// ─── 生成结果保存到剧本弹层 ───────────────────────────


