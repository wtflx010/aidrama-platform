import {useState, useEffect} from 'react'
import {Button} from '../ui/Button'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import {TOOL_GROUPS, TOOL_LABELS} from './shared'


export function ToolWhitelistDialog({
  open,
  current,
  onClose,
  onSave,
}: {
  open: boolean
  current: string[] | null
  onClose: () => void
  onSave: (whitelist: string[] | null) => void
}) {
  // 初始值：当前白名单（null → 全部可用模式）
  const [mode, setMode] = useState<'all' | 'list'>(current ? 'list' : 'all')
  const [selected, setSelected] = useState<string[]>(current ?? [])
  const [keyword, setKeyword] = useState('')
  // 每次打开时同步外部当前值
  useEffect(() => {
    if (open) {
      setMode(current ? 'list' : 'all')
      setSelected(current ?? [])
      setKeyword('')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  const kw = keyword.trim().toLowerCase()
  const allKeys = Object.keys(TOOL_LABELS)
  const toggle = (k: string) =>
    setSelected((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]))
  const groups = TOOL_GROUPS.map((g) => ({
    ...g,
    keys: g.keys.filter((k) => !kw || TOOL_LABELS[k]?.toLowerCase().includes(kw)),
  })).filter((g) => g.keys.length > 0)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[640px] max-h-[76vh] rounded-2xl bg-white shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="shield" size={14} className="text-amber-500" />
            会话工具权限
            <span className="text-[11px] font-normal text-slate-400">仅本会话生效 · 与角色白名单取交集</span>
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        {/* 模式切换 */}
        <div className="flex items-center gap-1.5 px-4 pt-3">
          <button
            type="button"
            onClick={() => setMode('all')}
            className={cn(
              'text-xs rounded-full px-3 py-1 border transition-colors',
              mode === 'all'
                ? 'bg-emerald-500 text-white border-emerald-500'
                : 'border-slate-200 text-slate-600 hover:border-brand-300',
            )}
          >
            全部工具可用
          </button>
          <button
            type="button"
            onClick={() => setMode('list')}
            className={cn(
              'text-xs rounded-full px-3 py-1 border transition-colors',
              mode === 'list'
                ? 'bg-amber-500 text-white border-amber-500'
                : 'border-slate-200 text-slate-600 hover:border-brand-300',
            )}
          >
            仅白名单内工具
          </button>
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索工具…"
            className="ml-auto w-32 input-base text-xs !py-1.5"
          />
        </div>
        {/* 工具列表 */}
        {mode === 'all' ? (
          <p className="text-xs text-slate-500 px-4 py-4">
            当前会话允许调用全部工具（{allKeys.length} 个内置工具 + Skill / MCP 动态工具）。若有高风险操作（git 提交/推送、终端命令、代码执行），模型仍会先征求你确认。
          </p>
        ) : (
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
            {groups.map((g) => (
              <div key={g.label}>
                <p className="text-[10px] font-medium text-slate-400 mb-1">{g.label}</p>
                <div className="flex flex-wrap gap-1">
                  {g.keys.map((k) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => toggle(k)}
                      className={cn(
                        'text-[11px] rounded-full px-2 py-0.5 border transition-colors',
                        selected.includes(k)
                          ? 'bg-amber-500 text-white border-amber-500'
                          : 'border-slate-200 text-slate-500 hover:border-amber-300 hover:text-amber-600',
                      )}
                    >
                      {TOOL_LABELS[k]}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {selected.length === 0 && (
              <p className="text-xs text-amber-600">未选择任何工具：模型将只能纯文本回答（所有工具均不可调用）。</p>
            )}
          </div>
        )}
        {/* 底部操作 */}
        <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between">
          <span className="text-[11px] text-slate-400">
            {mode === 'all' ? '所有工具' : `已选 ${selected.length} / ${allKeys.length} 个内置工具`}
          </span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button size="sm" onClick={() => onSave(mode === 'all' ? null : selected)}>
              保存
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── 长消息折叠（超长回复默认收起，减少滚动，Trae Work 对话流节点风格）────

