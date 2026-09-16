/**
 * 工作流卡片：创作规划（Plan）/ 创作目标（Goal）/ 小说大纲（Novel）。
 */
import {Button} from '../ui/Button'
import {ProgressBar} from '../ui/ProgressBar'
import {Icon} from '../../lib/icons'
import MarkdownRenderer from '../MarkdownRenderer'
import {cn} from '../../lib/cn'
import type {AgentGoal, AgentPlan, NovelOutline} from '../../api/types'

export function PlanCard({
  plan,
  onConfirm,
  onRunStep,
}: {
  plan: AgentPlan
  onConfirm?: () => void
  onRunStep?: (stepIndex: number) => void
}) {
  const steps = plan.steps ?? []
  const doneCount = steps.filter((s) => s.status === 'done').length
  const confirmed = plan.status !== 'draft'
  const allDone = plan.status === 'done'
  return (
    <div className="max-w-[560px]">
      <div className="rounded-2xl border border-brand-200 bg-white overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-brand-100 bg-brand-50/60">
          <Icon name="list" size={14} className="text-brand-500" />
          <span className="text-xs font-semibold text-brand-700">
            {plan.title}
          </span>
          <span
            className={cn(
              'text-[10px] font-medium rounded-full px-2 py-0.5 ml-auto',
              allDone
                ? 'bg-emerald-100 text-emerald-600'
                : confirmed
                  ? 'bg-brand-100 text-brand-600'
                  : 'bg-amber-100 text-amber-600',
            )}
          >
            {allDone ? '已完成' : confirmed ? '已确认' : '待确认'}
          </span>
        </div>
        <div className="p-3">
          <div className="md-body text-[13px] text-slate-700">
            <MarkdownRenderer content={plan.content} />
          </div>
          {/* 步骤列表 */}
          {steps.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {steps.map((s, i) => {
                const done = s.status === 'done'
                return (
                  <div
                    key={i}
                    className={cn(
                      'flex items-start gap-2 rounded-lg border px-2.5 py-2',
                      done ? 'border-emerald-100 bg-emerald-50/50' : 'border-slate-200 bg-slate-50/50',
                    )}
                  >
                    <span
                      className={cn(
                        'shrink-0 mt-0.5 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-semibold',
                        done ? 'bg-emerald-500 text-white' : 'bg-slate-200 text-slate-500',
                      )}
                    >
                      {done ? <Icon name="check" size={11} /> : i + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className={cn('text-xs font-medium', done ? 'text-emerald-700' : 'text-slate-700')}>
                        {s.title}
                      </p>
                      {s.description && (
                        <p className="text-[11px] text-slate-400 mt-0.5 leading-relaxed">{s.description}</p>
                      )}
                    </div>
                    {confirmed && !done && onRunStep && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onRunStep(i)}
                        className="shrink-0 !h-7 !px-2.5"
                      >
                        执行此步
                      </Button>
                    )}
                  </div>
                )
              })}
            </div>
          )}
          {/* 进度条 */}
          {confirmed && steps.length > 0 && (
            <div className="mt-3 flex items-center gap-2">
              <ProgressBar value={(doneCount / steps.length) * 100} variant="brand" />
              <span className="text-[11px] text-slate-400 shrink-0">
                {doneCount}/{steps.length} 步完成
              </span>
            </div>
          )}
          {/* 操作区 */}
          {!confirmed && onConfirm && (
            <div className="mt-3 flex justify-end">
              <Button size="sm" onClick={onConfirm} leftIcon={<Icon name="check" size={13} />}>
                确认规划，开始执行
              </Button>
            </div>
          )}
          {allDone && (
            <p className="mt-3 text-xs text-emerald-600 flex items-center gap-1.5">
              <Icon name="check-circle" size={13} />
              规划全部完成，可以继续提问或新建规划。
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── 创作目标卡片（Goal 工作流）──────────────────────

export function GoalCard({
  goal,
  onAdvance,
  onSetStatus,
}: {
  goal: AgentGoal
  onAdvance?: () => void
  onSetStatus?: (status: string) => void
}) {
  const done = goal.status === 'done'
  const paused = goal.status === 'paused'
  return (
    <div className="max-w-[560px]">
      <div className="rounded-2xl border border-violet-200 bg-white overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-violet-100 bg-violet-50/60">
          <Icon name="target" size={14} className="text-violet-500" />
          <span className="text-xs font-semibold text-violet-700">{goal.title}</span>
          <span
            className={cn(
              'text-[10px] font-medium rounded-full px-2 py-0.5 ml-auto',
              done
                ? 'bg-emerald-100 text-emerald-600'
                : paused
                  ? 'bg-slate-200 text-slate-500'
                  : 'bg-violet-100 text-violet-600',
            )}
          >
            {done ? '已达成' : paused ? '已暂停' : '进行中'}
          </span>
        </div>
        <div className="p-3 space-y-2.5">
          <div className="md-body text-[13px] text-slate-700">
            <MarkdownRenderer content={goal.goal} />
          </div>
          {goal.criteria && (
            <div className="rounded-lg bg-slate-50 border border-slate-100 px-2.5 py-2">
              <p className="text-[10px] font-medium text-slate-400 mb-0.5">完成标准</p>
              <p className="text-xs text-slate-600 leading-relaxed">{goal.criteria}</p>
            </div>
          )}
          {/* 进度 */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-400 shrink-0">已推进 {goal.step_count} 轮</span>
            {goal.progress_summary && !done && (
              <span className="text-[11px] text-violet-600 truncate">{goal.progress_summary}</span>
            )}
          </div>
          {/* 操作岛台 */}
          <div className="flex items-center gap-1.5 pt-1">
            {!done && !paused && onAdvance && (
              <Button size="sm" onClick={onAdvance} leftIcon={<Icon name="play" size={12} />}>
                推进一轮
              </Button>
            )}
            {!done && (
              <>
                {onSetStatus && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onSetStatus(paused ? 'active' : 'paused')}
                  >
                    {paused ? '恢复' : '暂停'}
                  </Button>
                )}
                {onSetStatus && (
                  <Button size="sm" variant="ghost" onClick={() => onSetStatus('done')}>
                    标记完成
                  </Button>
                )}
              </>
            )}
          </div>
          {done && (
            <p className="text-xs text-emerald-600 flex items-center gap-1.5">
              <Icon name="check-circle" size={13} />
              目标已达成，可以设定新目标或继续提问。
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── 长篇小说大纲卡片（/novel 工作流：预览 → 确认开始写作）──────

export function NovelOutlineCard({ outline, onStart }: { outline: NovelOutline; onStart?: () => void }) {
  return (
    <div className="max-w-[560px]">
      <div className="rounded-2xl border border-brand-200 bg-white overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-brand-100 bg-brand-50/60">
          <Icon name="book" size={14} className="text-brand-500" />
          <span className="text-xs font-semibold text-brand-700">{outline.title}</span>
          <span className="text-[10px] font-medium rounded-full px-2 py-0.5 bg-brand-100 text-brand-600 ml-auto">
            {outline.genre}
          </span>
        </div>
        <div className="p-3 space-y-2.5">
          <p className="text-[13px] text-slate-700">{outline.logline}</p>
          {outline.world && (
            <div className="rounded-lg bg-slate-50 border border-slate-100 px-2.5 py-2">
              <p className="text-[10px] font-medium text-slate-400 mb-0.5">核心设定</p>
              <p className="text-xs text-slate-600 leading-relaxed">{outline.world}</p>
            </div>
          )}
          {/* 章节列表 */}
          <div className="space-y-1">
            {outline.chapters.map((c) => (
              <div
                key={c.index}
                className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/50 px-2.5 py-2"
              >
                <span className="shrink-0 mt-0.5 w-5 h-5 rounded-full bg-brand-500 text-white flex items-center justify-center text-[10px] font-semibold">
                  {c.index}
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-slate-700">{c.title}</p>
                  <p className="text-[11px] text-slate-400 mt-0.5 leading-relaxed">{c.brief}</p>
                </div>
              </div>
            ))}
          </div>
          {onStart && (
            <div className="flex justify-end pt-1">
              <Button size="sm" onClick={onStart} leftIcon={<Icon name="zap" size={13} />}>
                确认大纲，开始写作（{outline.chapters.length} 章）
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── 长篇小说写作进度卡片（轮询 /tasks/{id}）──────────

// 进度轮询卡片（NovelWriteCard / ScriptWriteCard / VideoDraftCard）已拆分至 ./AssistantCards
// ─── @ 引用上下文弹层（项目/剧本文档/历史会话/资产，Trae Work # 上下文风格）──────
