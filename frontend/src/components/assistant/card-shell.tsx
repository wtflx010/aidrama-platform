import {cn} from '../../lib/cn'


export function CardShell({
  label,
  running,
  failed,
  tag,
  children,
}: {
  label: string
  running: boolean
  failed?: boolean
  /** 附加状态标签（如并行子智能体的「并行」徽标） */
  tag?: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-100 bg-slate-50/70">
        <span
          className={cn(
            'w-1.5 h-1.5 rounded-full',
            failed ? 'bg-rose-500' : running ? 'bg-brand-500 animate-pulse' : 'bg-emerald-500',
          )}
        />
        <span className="text-xs font-medium text-slate-600">{label}</span>
        {tag && (
          <span className="text-[10px] font-medium text-brand-600 bg-brand-100/70 rounded-full px-1.5 py-0.5 leading-none">
            {tag}
          </span>
        )}
        {running && <span className="text-[10px] text-slate-400 ml-auto">{failed ? '失败' : '执行中…'}</span>}
        {!running && !failed && <span className="text-[10px] text-emerald-600 ml-auto">完成</span>}
      </div>
      <div className="p-3">{children}</div>
    </div>
  )
}

/** 代码结果展示：识别 diff 行（+/-）做红绿高亮，其余等宽文本（P8 编码工具通用） */
export function DiffText({ content }: { content: string }) {
  const lines = (content || '').split('\n')
  const hasDiff = lines.some((l) => l.startsWith('+') || l.startsWith('-'))
  if (!hasDiff) {
    return (
      <pre className="text-xs text-slate-700 whitespace-pre-wrap break-all font-mono max-h-[360px] overflow-auto">
        {content}
      </pre>
    )
  }
  return (
    <div className="text-xs font-mono rounded-lg bg-slate-950/95 text-slate-100 max-h-[360px] overflow-auto leading-5">
      {lines.map((l, i) => (
        <div
          key={i}
          className={cn(
            'whitespace-pre-wrap break-all px-2',
            l.startsWith('+') && 'bg-emerald-500/20 text-emerald-300',
            l.startsWith('-') && 'bg-rose-500/20 text-rose-300',
          )}
        >
          {l || ' '}
        </div>
      ))}
    </div>
  )
}

// ─── 创作规划卡片（Plan 工作流）──────────────────────

