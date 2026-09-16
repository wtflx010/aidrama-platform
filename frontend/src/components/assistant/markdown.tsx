import {useState} from 'react'
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import MarkdownRenderer from '../MarkdownRenderer'


export function FoldedMarkdown({ content, highlight }: { content: string; highlight?: string }) {
  const [expanded, setExpanded] = useState(false)
  const long = content.length > 800
  return (
    <div>
      <div className={cn('relative', long && !expanded && 'max-h-[360px] overflow-hidden')}>
        <MarkdownRenderer content={content} highlight={highlight} />
        {long && !expanded && (
          <div className="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-white to-transparent pointer-events-none" />
        )}
      </div>
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 text-xs font-medium text-brand-600 hover:underline"
        >
          {expanded ? '收起' : '展开全文'}
        </button>
      )}
    </div>
  )
}

/** 推理模型思考过程折叠区（仅本地流式展示，不入库）。
 * 2026-08-16：只展示「模型真实的思考内容」，去掉渲染的假状态——
 * - 无思考内容（空文本）→ 不渲染任何块
 * - 去掉脉冲「思考中」图标；改为展示实时字数「思考中 · N字」，
 *   依据只有一条：thinking 文本是否在真实增长
 */
export function ThinkingBlock({ text, active }: { text: string; active: boolean }) {
  // 2026-08-16：思考内容默认展开且流结束后不自动收起——此前 active=false（流结束/回放）
  // 时自动折叠，切换页面回来只看到一行标题，用户误以为思考内容「消失」。现在内容常开可见，
  // 用户可手动折叠；active 仅驱动「思考中 · N字」实时徽标。
  const [expanded, setExpanded] = useState(true)
  // 没有真实思考内容 → 不渲染（避免出现没有依据的「思考中」空壳）
  if (!text || !text.trim()) return null
  return (
    <div className="mb-2 rounded-lg bg-amber-50/70 border border-amber-200/60 overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-amber-700 hover:bg-amber-100/60 transition-colors"
      >
        <Icon name="brain" size={13} />
        <span className="font-medium">思考过程</span>
        {active && (
          <span className="tabular-nums text-amber-500">
            {text.length > 0 ? `思考中 · ${text.length} 字` : '思考中'}
          </span>
        )}
        <span
          className={cn('ml-auto text-amber-500/70 transition-transform', expanded && 'rotate-180')}
        >
          <Icon name="chevron-down" size={12} />
        </span>
      </button>
      {expanded && (
        <div className="px-2.5 pb-2">
          <p className="text-xs leading-relaxed text-amber-700/80 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
            {text}
            {active && (
              <span className="inline-block w-1.5 h-3 bg-amber-500/60 align-middle ml-0.5 animate-pulse" />
            )}
          </p>
        </div>
      )}
    </div>
  )
}

