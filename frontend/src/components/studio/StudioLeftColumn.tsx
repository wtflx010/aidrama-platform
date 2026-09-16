/** 分镜工作台·左列：幕（可折叠）+ 幕内分镜列表。
 *  样式与内容沿用旧版项目详情页左列（分镜卡片：幕-镜编号 / 镜别 / 时长 / 描述），
 *  幕标题去掉「第N幕/第N集」序号前缀只保留幕名；分镜序号从分镜本身起算（1-1），
 *  只按幕、镜的实际顺序编号，不引用幕标题。 */
import { Icon } from '../../lib/icons'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import type { Episode, Segment, VideoClip } from '../../api/types'

/** 去掉「第N幕/第N集 」式序号前缀，只保留幕标题（如「第1幕 夜店救人」→「夜店救人」） */
function actLabel(ep: Episode): string {
  const raw = (ep.title || '').trim()
  const m = raw.match(/^(第\s*\d+\s*[幕集]\s*)(.*)$/s)
  if (m && m[2]?.trim()) return m[2].trim()
  return raw
}

export function StudioLeftColumn({ episodes, segments, collapsedEps, toggleEp, selectedId, onSelect, clips }: {
  episodes: Episode[]
  segments: Segment[]
  collapsedEps: Set<string>
  toggleEp: (id: string) => void
  selectedId: string | null
  onSelect: (id: string) => void
  clips: Record<string, VideoClip[]>
}) {
  // 仅展示含分镜的幕
  const hasSegIds = new Set(segments.map((s) => s.episode_id))
  const visibleEps = episodes.filter((ep) => hasSegIds.has(ep.id))

  return (
    <aside className="w-64 shrink-0 rounded-xl border border-slate-200 bg-white flex flex-col overflow-hidden">
      <div className="p-2 border-b border-slate-200 text-xs font-semibold text-slate-500 flex items-center gap-1.5">
        <Icon name="film" size={13} /> 分镜信息（{segments.length} 镜）
      </div>

      {/* 幕 + 幕内分镜（幕可折叠，只显示幕标题；分镜序号 = 幕序-镜序） */}
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {visibleEps.map((ep) => {
          const epSegs = segments
            .filter((s) => s.episode_id === ep.id)
            .sort((a, b) => a.index - b.index)
          const collapsed = collapsedEps.has(ep.id)
          return (
            <div key={ep.id}>
              <button
                onClick={() => toggleEp(ep.id)}
                className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-left text-sm font-semibold text-slate-700 hover:bg-slate-100 transition-colors"
                title={ep.title}
              >
                <Icon
                  name="chevron-down"
                  size={13}
                  className={cn('text-slate-400 transition-transform shrink-0', collapsed && '-rotate-90')}
                />
                <span className="truncate">{actLabel(ep)}</span>
                <span className="ml-auto text-[11px] font-normal text-slate-400 shrink-0">{epSegs.length} 镜</span>
              </button>
              {!collapsed && (
                <div className="mt-1 space-y-1">
                  {epSegs.map((s) => {
                    const video = (clips[s.id] || []).find((v) => v.status === 'succeeded')
                    return (
                      <button
                        key={s.id}
                        onClick={() => onSelect(s.id)}
                        className={cn(
                          'w-full text-left p-2.5 rounded-lg border transition-all',
                          selectedId === s.id
                            ? 'bg-brand-500/10 border-brand-500/30'
                            : 'border-transparent hover:bg-slate-100',
                        )}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          {/* 分镜序号：只按幕序(ep.index)与镜序(s.index)计算，如 1-1 */}
                          <span className="text-xs font-mono text-slate-400">{ep.index + 1}-{s.index}</span>
                          {s.shot_type && <Badge variant="blue" size="sm">{s.shot_type}</Badge>}
                          {video && <Icon name="check-circle" size={13} className="text-emerald-500 shrink-0" />}
                          <span className="text-xs text-slate-400 ml-auto">{s.duration}s</span>
                        </div>
                        <p className="text-xs text-slate-500 line-clamp-1">
                          {s.description || '（无画面描述）'}
                        </p>
                        {s.narration && (
                          <p className="text-[11px] text-violet-700/90 leading-snug mt-1 line-clamp-2">
                            <span className="font-medium text-violet-500">旁白：</span>{s.narration}
                          </p>
                        )}
                        {((s.dialogue_lines && s.dialogue_lines.length > 0) || s.dialogue) && (
                          <div className="mt-1 space-y-0.5">
                            {s.dialogue_lines && s.dialogue_lines.length > 0
                              ? s.dialogue_lines.map((dl, di) => (
                                  <p key={di} className="text-[11px] text-slate-700 leading-snug line-clamp-1">
                                    <span className="text-brand-600 font-medium">{dl.speaker}</span>：{dl.text}
                                  </p>
                                ))
                              : s.dialogue && (
                                  <p className="text-[11px] text-slate-700 leading-snug line-clamp-2">对白：{s.dialogue}</p>
                                )}
                          </div>
                        )}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
        {visibleEps.length === 0 && (
          <p className="text-xs text-slate-400 text-center py-6">暂无分镜</p>
        )}
      </div>
    </aside>
  )
}
