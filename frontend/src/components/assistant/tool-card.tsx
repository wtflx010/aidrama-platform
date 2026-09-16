/**
 * 工具执行结果卡片（生图/视频/项目/搜索/代码等）。
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '../ui/Button'
import { Icon } from '../../lib/icons'
import { cn } from '../../lib/cn'
import MarkdownRenderer from '../MarkdownRenderer'
import { CardShell, DiffText } from './card-shell'
import { ProjectDraftCard, ProjectDeleteCard } from './project-cards'
import { TOOL_LABELS, parseSearchItems } from './shared'
import type { ConfirmProjectOpts, LocalMsg, ProjectDraftPayload, ProjectDeletePayload } from './shared'

import { NovelWriteCard, ScriptWriteCard, VideoDraftCard } from '../AssistantCards'

export function ToolCard({
  msg,
  onSaveImage,
  onConfirmGitCommit,
  onConfirmGitPush,
  onConfirmProject,
  onConfirmProjectDelete,
}: {
  msg: LocalMsg
  onSaveImage?: (url: string) => void
  onConfirmGitCommit?: (info?: { files?: string[]; message?: string }) => void
  onConfirmGitPush?: (info?: { remote?: string; branch?: string }) => void
  onConfirmProject?: (msg: LocalMsg, opts: ConfirmProjectOpts) => void
  onConfirmProjectDelete?: (msg: LocalMsg) => void
}) {
  const name = msg.toolName ?? ''
  const label = TOOL_LABELS[name] ?? name
  const [expanded, setExpanded] = useState(false)
  if (name === 'generate_image') {
    return (
      <div className="max-w-[380px]">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          {msg.mediaUrls?.[0] ? (
            <>
              <img
                src={msg.mediaUrls[0]}
                alt="生成图片"
                className="w-full rounded-lg border border-slate-200 cursor-zoom-in"
                onClick={() => window.open(msg.mediaUrls![0], '_blank')}
              />
              {onSaveImage && (
                <button
                  type="button"
                  onClick={() => onSaveImage(msg.mediaUrls![0])}
                  className="mt-2 w-full flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg border border-slate-200 py-1.5 text-slate-600 hover:text-brand-600 hover:border-brand-300 hover:bg-brand-50 transition-colors"
                >
                  <Icon name="download" size={12} />
                  保存到剧本
                </button>
              )}
            </>
          ) : (
            <div className="h-32 flex items-center justify-center flex-col gap-1.5">
              <Icon name="loader-2" size={20} className="animate-spin text-brand-500" />
              <span className="text-[11px] text-slate-400">{msg.toolStep || '生成中…'}</span>
              {msg.content && <span className="text-[11px] text-rose-500 px-2 text-center">{msg.content}</span>}
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'browser_screenshot') {
    return (
      <div className="max-w-[520px]">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          {msg.mediaUrls?.[0] ? (
            <img
              src={msg.mediaUrls[0]}
              alt="浏览器截图"
              className="w-full rounded-lg border border-slate-200 cursor-zoom-in"
              onClick={() => window.open(msg.mediaUrls![0], '_blank')}
            />
          ) : (
            <div className="h-24 flex items-center justify-center flex-col gap-1.5">
              <Icon name="loader-2" size={18} className="animate-spin text-brand-500" />
              <span className="text-[11px] text-slate-400">{msg.toolStep || '截图生成中…'}</span>
              {msg.content && <span className="text-[11px] text-rose-500 px-2 text-center">{msg.content}</span>}
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'generate_video') {
    const draftId = (msg.toolParams?.draft_id as string) || ''
    return (
      <div className="max-w-[380px]">
        <CardShell label={label} running>
          <VideoDraftCard draftId={draftId} />
        </CardShell>
      </div>
    )
  }
  if (name === 'create_project') {
    // 项目草案（pending 确认）：读取会话剧本内容后等待用户确认（画幅/风格/内容预览）
    const draftPayload = (msg.toolParams as { pending_project_draft?: ProjectDraftPayload } | undefined)?.pending_project_draft
    if (draftPayload && !msg.projectId) {
      return <ProjectDraftCard msg={msg} payload={draftPayload} onConfirm={onConfirmProject} />
    }
    return (
      <div className="max-w-[340px]">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          {msg.projectId ? (
            <Link
              to={`/projects/${msg.projectId}`}
              className="flex items-center gap-2 text-brand-600 hover:underline text-sm"
            >
              <Icon name="folder" size={15} />
              进入项目查看 →
            </Link>
          ) : (
            <div className="text-slate-400 text-sm flex items-center gap-2">
              <Icon name="loader-2" size={15} className="animate-spin" />
              正在整理项目草案…
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'project_delete') {
    // 删除项目（pending 确认）：展示项目名+内容统计，用户确认后才真正删除
    const delPayload = (msg.toolParams as { pending_project_delete?: ProjectDeletePayload } | undefined)?.pending_project_delete
    const deleted = (msg.toolParams as { project_deleted?: boolean } | undefined)?.project_deleted
    if (delPayload && !deleted) {
      return <ProjectDeleteCard msg={msg} payload={delPayload} onConfirm={onConfirmProjectDelete} />
    }
    return (
      <div className="max-w-[340px]">
        <CardShell label={label} running={false} failed={msg.toolStatus === 'failed'}>
          <div className="text-sm text-slate-600 flex items-center gap-2">
            <Icon name="check" size={15} className="text-emerald-500" />
            {msg.content || (delPayload ? `项目「${delPayload.title}」已删除` : '已处理')}
          </div>
        </CardShell>
      </div>
    )
  }
  if (name === 'web_search' || name === 'ai.web_search') {
    const items = parseSearchItems(msg.content ?? '')
    // 完成态默认折叠为单行（对齐 dsh：工具一行、正文为主），点击展开来源列表
    if (msg.toolStatus === 'succeeded' && items.length > 0) {
      return (
        <div className="max-w-[520px]">
          <button type="button" onClick={() => setExpanded((v) => !v)} className="w-full text-left">
            <CardShell label={label} running={false} failed={false} tag={`${items.length} 条来源`}>
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span className="flex items-center gap-1">
                  <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={12} />
                  {expanded ? '收起来源' : '点击查看来源'}
                </span>
                <span className="flex items-center gap-1 text-emerald-500">
                  <Icon name="check" size={12} />
                  完成
                </span>
              </div>
              {expanded && (
                <ul className="mt-2 pt-2 border-t border-slate-100 divide-y divide-slate-100">
                  {items.map((it, i) => (
                    <li key={i} className="py-2">
                      <a
                        href={it.url || '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm font-medium text-brand-600 hover:underline break-all"
                      >
                        {it.title || it.url || '(无标题)'}
                      </a>
                      {it.snippet && (
                        <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">{it.snippet}</p>
                      )}
                      {it.url && (
                        <p className="text-[11px] text-slate-400 mt-0.5 truncate font-mono">{it.url}</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </CardShell>
          </button>
        </div>
      )
    }
    return (
      <div className="max-w-[520px]">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          {msg.toolStatus === 'running' ? (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Icon name="loader-2" size={14} className="animate-spin text-brand-500" />
              {msg.toolStep || '搜索中…'}
            </div>
          ) : (
            <pre className="text-xs text-slate-600 whitespace-pre-wrap font-mono">{msg.content}</pre>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'write_novel') {
    const taskId = (msg.toolParams?.task_id as string) || ''
    if (!taskId) {
      return (
        <CardShell label={label} running>
          <p className="text-xs text-slate-400 flex items-center gap-1.5">
            <Icon name="loader-2" size={13} className="animate-spin" />
            正在提交写作任务…
          </p>
        </CardShell>
      )
    }
    return (
      <NovelWriteCard
        writing={{
          novelId: (msg.toolParams?.novel_id as string) || '',
          taskId,
          title: (msg.toolParams?.title as string) || '',
          chapters: Number(msg.toolParams?.chapters) || 10,
        }}
      />
    )
  }
  if (name === 'write_script') {
    const taskId = (msg.toolParams?.task_id as string) || ''
    if (!taskId) {
      return (
        <CardShell label={label} running>
          <p className="text-xs text-slate-400 flex items-center gap-1.5">
            <Icon name="loader-2" size={13} className="animate-spin" />
            正在提交剧本写作任务…
          </p>
        </CardShell>
      )
    }
    return (
      <ScriptWriteCard
        writing={{
          taskId,
          novelId: (msg.toolParams?.novel_id as string) || '',
          title: (msg.toolParams?.title as string) || '',
          episodes: Number(msg.toolParams?.episodes) || 10,
        }}
      />
    )
  }
  if (name === 'git_commit') {
    const pending = msg.toolParams?.pending_git_commit as { files?: string[]; message?: string } | undefined
    const confirmed = msg.toolStatus === 'succeeded'
    const failed = msg.toolStatus === 'failed'
    return (
      <div className="max-w-[560px]">
        <CardShell label={label} running={!confirmed && !failed} failed={failed}>
          {confirmed ? (
            <div className="flex items-center gap-1.5 text-xs text-emerald-600">
              <Icon name="check-circle" size={13} />
              {msg.content || '已提交'}
            </div>
          ) : failed ? (
            <p className="text-xs text-rose-500">{msg.content}</p>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-slate-600">
                提交 {pending?.files?.length ?? 0} 个文件
                {pending?.files?.length ? `（${pending.files.slice(0, 4).join('、')}${pending.files.length > 4 ? '…' : ''}）` : '（全部改动）'}
                ，提交信息：「{pending?.message ?? ''}」
              </p>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => onConfirmGitCommit?.(pending)}
                  leftIcon={<Icon name="check" size={12} />}
                >
                  确认提交
                </Button>
                <span className="text-[11px] text-slate-400">确认后将执行 git add + commit</span>
              </div>
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'git_push') {
    // P9 Git 补全：推送确认卡（对标 git_commit 确认制）
    const pending = msg.toolParams?.pending_git_push as { remote?: string; branch?: string } | undefined
    const confirmed = msg.toolStatus === 'succeeded'
    const failed = msg.toolStatus === 'failed'
    return (
      <div className="max-w-[520px]">
        <CardShell label={label} running={!confirmed && !failed} failed={failed}>
          {confirmed ? (
            <div className="flex items-center gap-1.5 text-xs text-emerald-600">
              <Icon name="check-circle" size={13} />
              {msg.content || '已推送'}
            </div>
          ) : failed ? (
            <p className="text-xs text-rose-500">{msg.content}</p>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-slate-600">
                推送到 <code className="font-mono text-brand-600">{pending?.remote ?? 'origin'}</code>/
                <code className="font-mono text-brand-600">{pending?.branch || '当前分支'}</code>
              </p>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => onConfirmGitPush?.(pending)}
                  leftIcon={<Icon name="check" size={12} />}
                >
                  确认推送
                </Button>
                <span className="text-[11px] text-slate-400">确认后将执行 git push（涉及远程，请谨慎）</span>
              </div>
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'code_search') {
    // 计划 2.3：代码搜索结果列表（文件:行号: 内容片段）
    const lines = (msg.content || '').split('\n')
    const body = lines.slice(lines[0]?.startsWith('共') ? 1 : 0)
    return (
      <div className="max-w-[720px] w-full">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          {msg.toolStatus === 'succeeded' ? (
            <div className="space-y-1 max-h-[320px] overflow-auto rounded-lg bg-slate-950/95 p-2 text-[11px] font-mono">
              <p className="text-slate-400 px-1 pb-1 border-b border-slate-800">{lines[0]}</p>
              {body.map((l, i) => {
                const m = l.match(/^(.+?):(\d+):\s?(.*)$/)
                if (!m) return <p key={i} className="text-slate-300 whitespace-pre-wrap break-all px-1 py-0.5">{l}</p>
                return (
                  <p key={i} className="flex gap-2 px-1 py-0.5 hover:bg-slate-800/60 rounded">
                    <span className="text-brand-400 whitespace-nowrap">{m[1]}</span>
                    <span className="text-slate-500 whitespace-nowrap">{m[2]}</span>
                    <span className="text-slate-200 whitespace-pre-wrap break-all">{m[3]}</span>
                  </p>
                )
              })}
            </div>
          ) : msg.toolStatus === 'failed' ? (
            <p className="text-xs text-rose-500">{msg.content}</p>
          ) : (
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Icon name="loader-2" size={15} className="animate-spin" />
              {msg.toolStep || '搜索中…'}
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'code_diagnose') {
    // 计划 2.3：诊断列表（错误红 / 警告黄）
    const raw = msg.content || ''
    const summary = raw.split('\n')[0] ?? ''
    const items = raw.split('\n').slice(1)
    return (
      <div className="max-w-[720px] w-full">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          {msg.toolStatus === 'succeeded' ? (
            <div className="space-y-1.5">
              <p className={cn('text-xs', summary.includes('未发现') ? 'text-emerald-600' : 'text-slate-600')}>
                {summary}
              </p>
              {summary.includes('未发现') ? null : (
                <div className="max-h-[300px] overflow-auto rounded-lg bg-slate-50 border border-slate-200 divide-y divide-slate-100">
                  {items.map((l, i) => {
                    const isError = /Error|错误|失败|解析失败|SyntaxError|: [Ee]rror/.test(l)
                    return (
                      <div key={i} className={cn('flex gap-1.5 px-2 py-1 text-[11px] font-mono', isError ? 'text-rose-600' : 'text-amber-600')}>
                        <span>{isError ? '✕' : '⚠'}</span>
                        <span className="whitespace-pre-wrap break-all">{l}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          ) : msg.toolStatus === 'failed' ? (
            <p className="text-xs text-rose-500">{msg.content}</p>
          ) : (
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Icon name="loader-2" size={15} className="animate-spin" />
              {msg.toolStep || '诊断中…'}
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name === 'code_execute') {
    // 计划 2.3：沙箱执行终端样式（退出码 + stdout/stderr）
    const raw = msg.content || ''
    const m = raw.match(/^退出码 (\d+)\n([\s\S]*)$/)
    const exitCode = m ? Number(m[1]) : null
    const body = m ? m[2] : raw
    return (
      <div className="max-w-[720px] w-full">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          {msg.toolStatus === 'succeeded' ? (
            <div className="rounded-lg bg-slate-950 text-slate-100 overflow-hidden">
              <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-slate-800">
                <span className="w-2 h-2 rounded-full bg-rose-500/80" />
                <span className="w-2 h-2 rounded-full bg-amber-400/80" />
                <span className="w-2 h-2 rounded-full bg-emerald-500/80" />
                <span className="text-[10px] text-slate-500 ml-1">code_execute</span>
                {exitCode !== null && (
                  <span className={cn('text-[10px] ml-auto font-mono', exitCode === 0 ? 'text-emerald-400' : 'text-rose-400')}>
                    exit {exitCode}
                  </span>
                )}
              </div>
              <pre className="text-[11px] font-mono whitespace-pre-wrap break-all px-3 py-2 max-h-[320px] overflow-auto">
                {body}
              </pre>
            </div>
          ) : msg.toolStatus === 'failed' ? (
            <p className="text-xs text-rose-500">{msg.content}</p>
          ) : (
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Icon name="loader-2" size={15} className="animate-spin" />
              {msg.toolStep || '执行中…'}
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (
    name === 'code_list' ||
    name === 'code_read' ||
    name === 'code_edit' ||
    name === 'code_write' ||
    name === 'code_rollback' ||
    name === 'git_status' ||
    name === 'git_diff' ||
    name === 'git_log' ||
    name === 'git_branch'
  ) {
    return (
      <div className="max-w-[720px] w-full">
        <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
          <DiffText content={msg.content} />
        </CardShell>
      </div>
    )
  }
  if (name === 'tts_speak') {
    // P8 Phase 4 语音输出：合成过程分阶段展示（准备→合成→保存），完成后展示可播放音频
    const audioUrl = msg.mediaUrls?.[0]
    const done = msg.toolStatus === 'succeeded' && !!audioUrl
    const failed = msg.toolStatus === 'failed'
    const previewText = (msg.toolParams?.text as string) || ''
    return (
      <div className="max-w-[420px] w-full">
        <CardShell label={label} running={!done && !failed} failed={failed}>
          {done ? (
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 text-xs text-emerald-600">
                <Icon name="volume-2" size={13} />
                语音已生成
              </div>
              <audio controls src={audioUrl} className="w-full h-9" />
            </div>
          ) : failed ? (
            <p className="text-xs text-rose-500">{msg.content || '语音合成失败'}</p>
          ) : (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-slate-400 text-sm">
                <Icon name="loader-2" size={15} className="animate-spin" />
                {msg.toolStep || '语音合成中…'}
              </div>
              {previewText && (
                <p className="text-[11px] text-slate-400 line-clamp-2 leading-snug">
                  正在朗读：「{previewText}」
                </p>
              )}
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  if (name.startsWith('subagent_')) {
    // P9 子智能体并行化：并行执行的卡片带「并行」标签（结构化标记，随 toolParams 持久化）；产出用 Markdown 渲染
    // 宽度自适应撑满消息体（与主智能体对话框宽度一致）
    const parallel = msg.toolParams?.parallel === true
    return (
      <div className="w-full">
        <CardShell
          label={label}
          running={msg.toolStatus !== 'succeeded'}
          failed={msg.toolStatus === 'failed'}
          tag={parallel ? '并行' : undefined}
        >
          {msg.toolStatus === 'succeeded' ? (
            <div className="md-body text-[13px] text-slate-700 max-h-[420px] overflow-auto">
              <MarkdownRenderer content={msg.content} />
            </div>
          ) : msg.toolStatus === 'failed' ? (
            <p className="text-xs text-rose-500">{msg.content}</p>
          ) : (
            <div className="flex items-center gap-2 text-slate-400 text-sm">
              <Icon name="loader-2" size={15} className="animate-spin" />
              {msg.toolStep || '子智能体思考中…'}
            </div>
          )}
        </CardShell>
      </div>
    )
  }
  // 通用输出类工具（bash/read/grep/terminal 等）完成态默认折叠为单行，点击展开输出
  const content = msg.content ?? ''
  if (msg.toolStatus === 'succeeded' && content.trim()) {
    return (
      <CardShell label={label} running={false} failed={false}>
        <button type="button" onClick={() => setExpanded((v) => !v)} className="w-full text-left">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span className="flex items-center gap-1">
              <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={12} />
              {expanded ? '收起输出' : '查看输出'}
            </span>
            <span className="flex items-center gap-1 text-emerald-500">
              <Icon name="check" size={12} />
              完成
            </span>
          </div>
          {expanded && (
            <pre className="mt-2 pt-2 border-t border-slate-100 text-xs text-slate-600 whitespace-pre-wrap font-mono max-h-[320px] overflow-auto">
              {content}
            </pre>
          )}
        </button>
      </CardShell>
    )
  }
  return (
    <CardShell label={label} running={msg.toolStatus !== 'succeeded'} failed={msg.toolStatus === 'failed'}>
      <pre className="text-xs text-slate-600 whitespace-pre-wrap font-mono">{msg.content}</pre>
    </CardShell>
  )
}

/** 项目创建确认卡：展示剧本内容草案（角色/场景/道具/分镜），选择画幅与美术风格后确认创建 */

