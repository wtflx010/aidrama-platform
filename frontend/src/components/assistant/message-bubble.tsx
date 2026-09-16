/**
 * 消息气泡（含时序块渲染：文本 + 工具卡交错）。
 */
import {Icon} from '../../lib/icons'
import {cn} from '../../lib/cn'
import {FoldedMarkdown, ThinkingBlock} from './markdown'
import {AttachmentPreview} from './attachment'
import {ToolCard} from './tool-card'
import {PlanCard} from './workflow-cards'
import {GoalCard} from './workflow-cards'
import {NovelOutlineCard} from './workflow-cards'
import type { ConfirmProjectOpts, LocalMsg } from './shared'

import { NovelWriteCard } from '../AssistantCards'

export function MessageBubble({
  msg,
  onCopy,
  onRegenerate,
  onSaveImage,
  onSpeak,
  ttsReady,
  ttsReason,
  highlightKw,
  onConfirmPlan,
  onRunPlanStep,
  onAdvanceGoal,
  onSetGoalStatus,
  onFork,
  onStartNovel,
  onConfirmGitCommit,
  onConfirmGitPush,
  onConfirmProject,
  onConfirmProjectDelete,
}: {
  msg: LocalMsg
  onCopy?: (content: string) => void
  onRegenerate?: (msgKey: string) => void
  onSaveImage?: (url: string) => void
  onSpeak?: (content: string) => void
  /** P8 Phase 4：TTS 服务可用性（不可用时朗读按钮置灰） */
  ttsReady?: boolean
  ttsReason?: string
  /** 会话搜索命中关键词：仅对当前定位目标消息标亮 */
  highlightKw?: string
  onConfirmPlan?: () => void
  onRunPlanStep?: (stepIndex: number) => void
  onAdvanceGoal?: () => void
  onSetGoalStatus?: (status: string) => void
  onFork?: () => void
  onStartNovel?: () => void
  onConfirmGitCommit?: (info?: { files?: string[]; message?: string }) => void
  onConfirmGitPush?: (info?: { remote?: string; branch?: string }) => void
  /** 项目草案确认：选择画幅/风格后创建完整项目 */
  onConfirmProject?: (msg: LocalMsg, opts: ConfirmProjectOpts) => void
  /** 项目删除确认：用户确认后真正删除 */
  onConfirmProjectDelete?: (msg: LocalMsg) => void
}) {
  // 工具/子智能体消息：与主智能体对话同列对齐（头像占位 + 消息体撑满），
  // 保证工具卡片左边缘与主对话气泡一致
  if (msg.role === 'tool') {
    return (
      <div className="flex gap-3">
        <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 bg-slate-100 text-slate-500">
          <Icon name={msg.toolName?.startsWith('subagent_') ? 'brain' : 'package'} size={14} />
        </div>
        <div className="min-w-0 flex-1">
          <ToolCard
            msg={msg}
            onSaveImage={onSaveImage}
            onConfirmGitCommit={onConfirmGitCommit}
            onConfirmGitPush={onConfirmGitPush}
            onConfirmProject={onConfirmProject}
            onConfirmProjectDelete={onConfirmProjectDelete}
          />
        </div>
      </div>
    )
  }
  if (msg.plan) return <PlanCard plan={msg.plan} onConfirm={onConfirmPlan} onRunStep={onRunPlanStep} />
  if (msg.goal) return <GoalCard goal={msg.goal} onAdvance={onAdvanceGoal} onSetStatus={onSetGoalStatus} />
  if (msg.novelOutline) return <NovelOutlineCard outline={msg.novelOutline} onStart={onStartNovel} />
  if (msg.novelWriting) return <NovelWriteCard writing={msg.novelWriting} />
  const isUser = msg.role === 'user'
  return (
    <div className={cn('flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row')}>
      {/* 头像 */}
      <div className={cn(
        'w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5',
        isUser
          ? 'bg-slate-200 text-slate-600'
          : 'bg-gradient-brand text-white shadow-sm',
      )}>
        <Icon name={isUser ? 'users' : 'sparkles'} size={14} />
      </div>
      {/* 消息体 */}
      <div className={cn('min-w-0', isUser ? 'max-w-[80%]' : 'flex-1')}>
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm break-words',
            isUser
              ? 'bg-slate-100 text-slate-800 rounded-tr-sm whitespace-pre-wrap'
              : 'text-slate-800',
          )}
        >
          {!isUser && msg.thinking && msg.thinking.trim() && (
            <ThinkingBlock
              text={msg.thinking}
              active={(!!msg.streaming && !msg.thinkingDone) || msg.completed === false}
            />
          )}
          {isUser ? (
            msg.content
          ) : (
            /* 1:1 对齐 dsh web：助手消息内按时间顺序交错渲染正文块与工具卡 */
            <div className="space-y-3">
              {msg.parts && msg.parts.length > 0 ? (
                msg.parts.map((p) => {
                  if (p.kind === 'text') {
                    return p.text ? (
                      <FoldedMarkdown key={p.key} content={p.text} highlight={highlightKw} />
                    ) : null
                  }
                  const cardMsg: LocalMsg = {
                    key: p.key,
                    role: 'tool',
                    content: p.content ?? '',
                    dbId: p.dbId,
                    toolName: p.toolName,
                    toolStatus: p.toolStatus,
                    toolStep: p.toolStep,
                    mediaUrls: p.mediaUrls,
                    projectId: p.projectId,
                    toolParams: p.toolParams,
                  }
                  return (
                    <ToolCard
                      key={p.key}
                      msg={cardMsg}
                      onSaveImage={onSaveImage}
                      onConfirmGitCommit={onConfirmGitCommit}
                      onConfirmGitPush={onConfirmGitPush}
                      onConfirmProject={onConfirmProject}
                      onConfirmProjectDelete={onConfirmProjectDelete}
                    />
                  )
                })
              ) : (
                <FoldedMarkdown content={msg.content} highlight={highlightKw} />
              )}
            </div>
          )}
          {!isUser && !msg.streaming && msg.completed === false && (
            <span className="inline-flex items-center gap-1.5 text-slate-400">
              <Icon name="alert-circle" size={13} className="text-amber-500" />
              <span className="text-xs">
                生成已中断，内容可能不完整（可重新发送）
              </span>
            </span>
          )}
          {!isUser && msg.streaming && !msg.content && !msg.parts?.length && (
            <span className="inline-flex items-center gap-1.5 text-slate-400">
              <span className="w-1.5 h-1.5 rounded-full bg-brand-400 animate-bounce" />
              <span className="w-1.5 h-1.5 rounded-full bg-brand-400 animate-bounce [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-brand-400 animate-bounce [animation-delay:300ms]" />
              <span className="text-xs ml-1">正在思考</span>
            </span>
          )}
          {isUser && msg.images && msg.images.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {msg.images.map((u, i) => (
                <img
                  key={i}
                  src={u}
                  alt="用户图片"
                  className="w-16 h-16 object-cover rounded-lg border border-slate-300 cursor-zoom-in"
                  onClick={() => window.open(u, '_blank')}
                />
              ))}
            </div>
          )}
          {isUser && msg.attachments && msg.attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {msg.attachments.map((att, i) => (
                <AttachmentPreview key={i} att={att} />
              ))}
            </div>
          )}
          {msg.streaming && <span className="md-caret" />}
          {msg.error && <div className="text-rose-500 text-xs mt-1">{msg.error}</div>}
        </div>
        {/* 助手消息操作栏（Trae Work 风格：消息下方） */}
        {!isUser && !msg.streaming && msg.content && (
          <div className="flex items-center gap-0.5 mt-1.5 ml-1">
            <button
              type="button"
              title={ttsReady ? '朗读（TTS）' : ttsReason || '语音合成服务不可用'}
              onClick={() => onSpeak?.(msg.content)}
              disabled={!ttsReady}
              className={cn(
                'p-1 rounded transition-colors',
                ttsReady
                  ? 'text-slate-400 hover:text-brand-600 hover:bg-slate-100'
                  : 'text-slate-300 cursor-not-allowed',
              )}
            >
              <Icon name="volume-2" size={13} />
            </button>
            <button
              type="button"
              title="复制"
              onClick={() => onCopy?.(msg.content)}
              className="p-1 rounded text-slate-400 hover:text-brand-600 hover:bg-slate-100"
            >
              <Icon name="copy" size={13} />
            </button>
            {onRegenerate && (
              <button
                type="button"
                title="重新生成"
                onClick={() => onRegenerate(msg.key)}
                className="p-1 rounded text-slate-400 hover:text-brand-600 hover:bg-slate-100"
              >
                <Icon name="refresh" size={13} />
              </button>
            )}
            {onFork && (
              <button
                type="button"
                title="创建会话副本（Fork）"
                onClick={onFork}
                className="p-1 rounded text-slate-400 hover:text-brand-600 hover:bg-slate-100"
              >
                <Icon name="git-branch" size={13} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── 工具卡片 ─────────────────────────────────────────


