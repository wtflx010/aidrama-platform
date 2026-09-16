/**
 * 创作助手（Agent）聊天页（/assistant，2026-08-11）
 *
 * 2026-08-17 重构：6367 行单体拆分——子组件/常量/纯函数已迁至
 * `src/components/assistant/`（shared + 19 个组件文件），本文件保留
 * 主组件编排 + 会话/消息状态 + SSE 流式处理逻辑（行为零变化）。
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, authHeaders } from '../api/client'
import type { AgentAttachment, AgentGoal, AgentMessage, AgentPlan, AgentPlugin, AgentSearchHit, AgentSession, AgentSSEEvent, ContextRef, Model, Novel, NovelOutline, NovelWriteResp, Project } from '../api/types'
import { Button } from './ui/Button'
import { useToast } from './ui/Toast'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'
// ── 拆分产物（2026-08-17）──
import { WelcomePanel } from './assistant/welcome'
import { ModelSelector } from './assistant/model-selector'
import { AttachmentPreview } from './assistant/attachment'
import { ToolWhitelistDialog } from './assistant/tool-whitelist'
import { MessageBubble } from './assistant/message-bubble'
import { ContextPickerDialog, SlashCommandMenu } from './assistant/context-picker'
import { SaveToNovelDialog } from './assistant/save-novel'
import { MemoryDialog } from './assistant/memory-dialog'
import { SkillDialog } from './assistant/skill-dialog'
import { McpServerDialog } from './assistant/mcp-dialog'
import { PluginSelector, PluginDialog } from './assistant/plugin-dialog'
import { RuleDialog } from './assistant/rule-dialog'
import { ScheduleDialog } from './assistant/schedule-dialog'
import { ATTACH_KIND_LABELS, TOOL_LABELS } from './assistant/shared'
import type { ConfirmProjectOpts, LocalMsg, AssistantPart } from './assistant/shared'

// 2026-08-16：空数组必须用模块常量（useQuery disabled 时 data 落到默认值，
// 字面量 `= []` 每次渲染新引用 → 依赖它的 effect 无限循环 → React Maximum update depth）。
const EMPTY_HISTORY: AgentMessage[] = []
const EMPTY_MODELS: Model[] = []
const EMPTY_PLUGINS: AgentPlugin[] = []
const EMPTY_SESSIONS: AgentSession[] = []
const EMPTY_ANY: Project[] = []
const EMPTY_NOVELS: Novel[] = []


export default function Assistant() {
  const qc = useQueryClient()
  const toast = useToast()

  // 会话与消息
  // 记住最后一次沟通的会话：切走页签（组件卸载）后返回仍默认选中该会话
  const [activeId, setActiveId] = useState<string | null>(
    () => sessionStorage.getItem('assistant-last-session'),
  )
  // 是否已完成首次会话定位（加载历史时瞬间到底部，仅流式输出时平滑滚动）
  const sessionPositionedRef = useRef(false)
  /** P8 Phase 3 会话历史搜索：关键词 + 命中结果 + 待定位消息 */
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<AgentSearchHit[]>([])
  const [focusMsgId, setFocusMsgId] = useState<string | null>(null)
  /** 会话搜索命中关键词：定位消息内标亮；焦点清除时一并清空 */
  const [highlightKw, setHighlightKw] = useState('')
  // 记住最后选中的会话：切走页签（组件卸载）后返回仍默认选中该会话
  useEffect(() => {
    if (activeId) sessionStorage.setItem('assistant-last-session', activeId)
  }, [activeId])
  // 搜索防抖：输入 300ms 后调用搜索接口，空关键词清空结果
  useEffect(() => {
    const kw = searchQuery.trim()
    if (!kw) {
      setSearchResults([])
      return
    }
    const t = setTimeout(async () => {
      try {
        const hits = await api.get<AgentSearchHit[]>(`/agent/sessions/search?q=${encodeURIComponent(kw)}&limit=20`)
        setSearchResults(hits)
      } catch {
        setSearchResults([])
      }
    }, 300)
    return () => clearTimeout(t)
  }, [searchQuery])
  const [messages, setMessages] = useState<LocalMsg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [modelId, setModelId] = useState<string>('')
  const [pendingImages, setPendingImages] = useState<string[]>([])
  /** P8 Phase 5 多模态附件：待发送附件（音频转写/视频抽帧/文档文本，上传后持有） */
  const [pendingAttachments, setPendingAttachments] = useState<AgentAttachment[]>([])
  /** P8 Phase 5 附件上传/转写中（按钮转圈提示） */
  const [uploading, setUploading] = useState(false)
  const [atPickerOpen, setAtPickerOpen] = useState(false)
  const [saveTarget, setSaveTarget] = useState<string | null>(null)
  const [listening, setListening] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  /** T6 创作工作台面板开关 */
  const [workbenchOpen, setWorkbenchOpen] = useState(false)
  /** Skill / MCP 管理弹层 */
  const [skillOpen, setSkillOpen] = useState(false)
  const [mcpOpen, setMcpOpen] = useState(false)
  /** 生成插件：选中插件名列表（对齐 TraeWork「选择插件 → 描述需求 → 执行」）+ 管理弹层 */
  const [activePlugins, setActivePlugins] = useState<string[]>([])
  const [pluginOpen, setPluginOpen] = useState(false)
  /** 规则管理弹层（对齐 TraeWork Rules） */
  const [ruleOpen, setRuleOpen] = useState(false)
  /** P8 定时自动化任务弹层 */
  const [scheduleOpen, setScheduleOpen] = useState(false)
  /** P8 Phase 4：TTS 服务可用性（朗读按钮置灰依据） */
  const [ttsReady, setTtsReady] = useState(true)
  const [ttsReason, setTtsReason] = useState('')
  /** P9 会话级工具权限弹层 */
  const [toolPermOpen, setToolPermOpen] = useState(false)
  /** @ 待发送的上下文引用（项目/文档/会话/资产），随消息一并提交 */
  const [pendingRefs, setPendingRefs] = useState<ContextRef[]>([])
  /** 上下文使用率（done 事件携带），展示在消息流底部 */
  const [ctxTokens, setCtxTokens] = useState<number | null>(null)
  /** 斜杠命令菜单 */
  const [slashOpen, setSlashOpen] = useState(false)
  /** P8 Phase 5：文件拖入输入框时高亮 */
  const [dragActive, setDragActive] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null) // 消息流滚动容器（切换会话直接定位底部）
  const messagesRef = useRef<LocalMsg[]>([])
  const voiceRecRef = useRef<unknown>(null)
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  const { data: sessions = EMPTY_SESSIONS } = useQuery<AgentSession[]>({
    queryKey: ['agent-sessions'],
    queryFn: () => api.get<AgentSession[]>('/agent/sessions'),
  })
  /** P9 当前会话的工具白名单（null=全部工具可用） */
  const currentWhitelist = sessions.find((s) => s.id === activeId)?.tool_whitelist ?? null

  const { data: textModels = EMPTY_MODELS } = useQuery<Model[]>({
    queryKey: ['agent-text-models'],
    queryFn: () => api.get<Model[]>('/models?model_type=text'),
  })

  // 生成插件列表（对齐 TraeWork：对话前选择插件 → 描述需求 → 执行）
  const { data: plugins = EMPTY_PLUGINS } = useQuery<AgentPlugin[]>({
    queryKey: ['agent-plugins'],
    queryFn: () => api.get<AgentPlugin[]>('/agent/plugins'),
  })

  // T6 创作工作台数据：项目 / 小说写作 / 进行中任务
  const { data: projects = EMPTY_ANY } = useQuery<Project[]>({
    queryKey: ['workbench-projects'],
    queryFn: () => api.get<Project[]>('/projects'),
    enabled: workbenchOpen,
  })
  const { data: novels = EMPTY_NOVELS } = useQuery<Novel[]>({
    queryKey: ['workbench-novels'],
    queryFn: () => api.get<Novel[]>('/novels'),
    enabled: workbenchOpen,
  })

  // 2026-08-16 修复：默认空数组必须是【模块级常量】——useQuery 未启用（activeId 为空）时
  // data 为 undefined 落到 `= []` 字面量，每次渲染都是新引用 → 下方「加载历史」effect
  // 依赖 [activeId, historyMessages] 每次渲染都变化 → 反复 setMessages → React
  //「Maximum update depth exceeded」无限循环 → 主线程被饿死 → 前端卡「思考中」、
  // 请求 15 秒无响应头。用 EMPTY_HISTORY 常量后引用稳定，循环即断。
  const { data: historyMessages = EMPTY_HISTORY } = useQuery<AgentMessage[]>({
    queryKey: ['agent-messages', activeId],
    queryFn: () => api.get<AgentMessage[]>(`/agent/sessions/${activeId}/messages`),
    enabled: !!activeId,
    // 切走后任务仍在后台继续执行（completed=false 占位实时落库 / tool 消息逐步落库）：
    // 切回会话后每 3 秒自动轮询直至服务端完成；轮询窗口 6 分钟覆盖后端总超时（300s）。
    // 同时覆盖 tool 消息（子智能体/后台工具）：assistant 已收尾但 tool 还在 running 时，
    // 若只按 completed=false 判断会停止轮询，导致切回后看不到后台工具的新消息（任务"消失"）。
    refetchInterval: (query) => {
      const msgs = query.state.data as AgentMessage[] | undefined
      const hasPending = msgs?.some((m) =>
        (m.role === 'assistant' && m.completed === false) ||
        (m.role === 'tool' && m.tool_status === 'running'),
      )
      if (!hasPending) return false
      const age = Date.now() - (query.state.dataUpdatedAt || 0)
      return age < 360_000 ? 3000 : false
    },
  })

  // 切换会话时加载历史
  useEffect(() => {
    if (!activeId) {
      setMessages([])
      return
    }
    sessionPositionedRef.current = false
    setMessages((cur) => {
      // 回放竞态防护：保留仍在 streaming 的本地消息（回放拉取期间用户新发的消息不被整体替换抹掉）
      const localLive = cur.filter((x) => x.streaming)
      if (localLive.length === 0) return groupHistory(historyMessages)
      return [...groupHistory(historyMessages), ...localLive]
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, historyMessages])

  // 切走中断的占位消息（completed=false）：轮询 6 分钟服务端仍未完成 →
  // 本地标记为已保留部分内容，停止「继续生成中/思考中」转圈态。
  // 2026-08-16 修复：计时器不再随轮询重建——此前的实现每次 historyMessages 变化
  // （占位轮询 3s 一拉）都 clearTimeout 重设 360s，兜底永远不触发，导致中断残留的
  // 占位消息「思考中/继续生成中」图标永久挂着。改为每条 pending 消息只安排一次
  // 一次性计时器（pendingTimersRef），到达即标记完成并收起状态。
  const pendingSinceRef = useRef<Record<string, number>>({})
  const pendingTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  useEffect(() => {
    if (!activeId) return
    const now = Date.now()
    const pendingIds = new Set(
      historyMessages
        .filter((m) => m.role === 'assistant' && m.completed === false)
        .map((m) => m.id),
    )
    // 清理已不再 pending 的消息（服务端完成/被删除）
    for (const id of Object.keys(pendingTimersRef.current)) {
      if (!pendingIds.has(id)) {
        clearTimeout(pendingTimersRef.current[id])
        delete pendingTimersRef.current[id]
        delete pendingSinceRef.current[id]
      }
    }
    for (const id of pendingIds) {
      if (pendingTimersRef.current[id]) continue // 已安排兜底，不重建（防轮询重置）
      pendingSinceRef.current[id] ??= now
      pendingTimersRef.current[id] = setTimeout(() => {
        setMessages((cur) =>
          cur.map((m) =>
            m.dbId === id ? { ...m, completed: true, thinkingDone: true } : m,
          ),
        )
        delete pendingTimersRef.current[id]
      }, 360_000)
    }
    return () => {
      // 会话切换/卸载时清理全部计时器，避免跨会话误标记
      if (pendingTimersRef.current) {
        for (const id of Object.keys(pendingTimersRef.current)) {
          clearTimeout(pendingTimersRef.current[id])
        }
        pendingTimersRef.current = {}
        pendingSinceRef.current = {}
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, historyMessages])

  // 滚动定位：首次加载历史/切换会话直接定位到底部（无滑动动画，抗浏览器滚动锚定）；
  // 流式输出时平滑滚动；有搜索定位目标（focusMsgId）时跳过自动滚底，交由定位逻辑处理
  useEffect(() => {
    if (focusMsgId || !bottomRef.current) return
    if (sessionPositionedRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
      return
    }
    const el = listRef.current
    if (el) {
      // 直接设 scrollTop（瞬时定位），双 rAF 确保图片/markdown 布局完成后仍停在底部
      const pin = () => {
        el.scrollTop = el.scrollHeight
      }
      pin()
      requestAnimationFrame(() => requestAnimationFrame(pin))
      sessionPositionedRef.current = true
    } else {
      bottomRef.current.scrollIntoView({ behavior: 'auto' })
      sessionPositionedRef.current = true
    }
  }, [messages, sending, focusMsgId])

  // P8 Phase 3：搜索跳转定位——目标消息渲染后滚动到视野中央并短暂高亮
  useEffect(() => {
    if (!focusMsgId) return
    const el = document.getElementById(`msg-${focusMsgId}`)
    if (!el) return // 消息尚未加载（切换会话拉历史中），等 messages 变化重试
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('ring-2', 'ring-brand-400/70', 'rounded-2xl')
    const t = setTimeout(() => {
      el.classList.remove('ring-2', 'ring-brand-400/70', 'rounded-2xl')
      setFocusMsgId(null)
      setHighlightKw('')
    }, 2600)
    return () => clearTimeout(t)
  }, [focusMsgId, messages])

  // 注意：切换路由/网页时【不】中止流 —— 让后端任务在后台继续执行并落库，
  // 切回会话时由上方 agent-messages 轮询自动续看（含 tool 消息），
  // 避免「切走 → 连接中断 → 内容丢失」。用户主动停止走 stop()。

  // P8 Phase 4：挂载时探测 TTS 服务可用性（CosyVoice 未在线/未配置模型 → 朗读按钮置灰）
  useEffect(() => {
    let alive = true
    api
      .get<{ ok: boolean; reason?: string }>('/agent/tts/health')
      .then((r) => {
        if (!alive) return
        setTtsReady(r.ok)
        setTtsReason(r.reason ?? '')
      })
      .catch(() => {
        if (alive) setTtsReady(false)
      })
    return () => {
      alive = false
    }
  }, [])

  /** 导出当前会话为 Markdown 文件 */
  const exportSession = () => {
    if (messages.length === 0) return
    const title = sessions.find((s) => s.id === activeId)?.title ?? '创作助手对话'
    const lines: string[] = [`# ${title}`, '']
    for (const m of messages) {
      if (m.role === 'user') {
        lines.push('## 用户')
        if (m.images?.length) m.images.forEach(() => lines.push('![用户图片]'))
        if (m.attachments?.length)
          m.attachments.forEach((a) => lines.push(`> [附件 ${ATTACH_KIND_LABELS[a.kind] ?? a.kind}] ${a.name}`))
        if (m.content) lines.push(m.content)
      } else if (m.role === 'assistant') {
        lines.push('## 助手')
        lines.push(m.content || '（无内容）')
        for (const p of m.parts ?? []) {
          if (p.kind !== 'tool') continue
          const label = TOOL_LABELS[p.toolName ?? ''] ?? p.toolName
          lines.push(`> [工具 ${label}] ${p.toolStatus === 'failed' ? '（失败）' : ''}`)
          if (p.mediaUrls?.length) lines.push(`> 图片: ${p.mediaUrls.join(', ')}`)
          if (p.projectId) lines.push(`> 项目: ${p.projectId}`)
        }
      } else if (m.role === 'tool') {
        const label = TOOL_LABELS[m.toolName ?? ''] ?? m.toolName
        lines.push(`> [工具 ${label}] ${m.toolStatus === 'failed' ? '（失败）' : ''}`)
        if (m.mediaUrls?.length) lines.push(`> 图片: ${m.mediaUrls.join(', ')}`)
        if (m.projectId) lines.push(`> 项目: ${m.projectId}`)
      }
      lines.push('')
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${title}.md`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('会话已导出')
  }

  const newSession = async () => {
    abortRef.current?.abort()
    const s = await api.post<AgentSession>('/agent/sessions', {})
    qc.invalidateQueries({ queryKey: ['agent-sessions'] })
    setActiveId(s.id)
    setMessages([])
    setInput('')
  }

  const deleteSession = async (id: string) => {
    await api.del(`/agent/sessions/${id}`)
    if (id === activeId) {
      // 切换/删除当前会话时中断进行中的流，避免旧流事件污染
      abortRef.current?.abort()
      setActiveId(null)
      setMessages([])
      setCtxTokens(null)
      sessionStorage.removeItem('assistant-last-session')
    }
    qc.invalidateQueries({ queryKey: ['agent-sessions'] })
  }

  /** 切换会话：先中断进行中的流，再切换（防止旧流 token 追加进新会话消息流） */
  const switchSession = (id: string) => {
    if (id === activeId) return
    abortRef.current?.abort()
    setActiveId(id)
    setCtxTokens(null)
    setMessages([])
    // 后端断连兜底保存需要落库时间：延迟刷新目标会话历史，切回时可看到已生成的部分回复
    window.setTimeout(() => {
      qc.invalidateQueries({ queryKey: ['agent-messages', id] })
    }, 1200)
  }

  /** P8 Phase 3：点击搜索结果 → 切到所属会话并定位高亮目标消息 */
  const openSearchHit = (hit: AgentSearchHit) => {
    setSearchQuery('')
    setSearchResults([])
    abortRef.current?.abort()
    setActiveId(hit.session_id)
    setCtxTokens(null)
    setMessages([])
    sessionPositionedRef.current = false
    setFocusMsgId(hit.message_id)
    setHighlightKw(searchQuery.trim())
    window.setTimeout(() => {
      qc.invalidateQueries({ queryKey: ['agent-messages', hit.session_id] })
    }, 1200)
  }

  const send = async () => {
    const text = input.trim()
    if ((!text && pendingImages.length === 0 && pendingAttachments.length === 0) || sending) return
    setInput('')
    const images = [...pendingImages]
    const refs = [...pendingRefs]
    const attachments = [...pendingAttachments]
    setPendingImages([])
    setPendingRefs([])
    setPendingAttachments([])
    // /plan 前缀 → 创作规划工作流（先出规划文档，确认后分步执行）
    if (text.startsWith('/plan')) {
      await runPlan(text.slice(5).trim() || text)
      return
    }
    // /goal 前缀 → 创作目标工作流（设定目标，AI 多轮自动推进 + 自评）
    if (text.startsWith('/goal')) {
      await runGoal(text.slice(5).trim() || text)
      return
    }
    // /novel 前缀 → 长篇小说工作流（先生成大纲预览，确认后后台逐章写作）
    if (text.startsWith('/novel')) {
      await runNovel(text.slice(6).trim() || text)
      return
    }
    const userMsg: LocalMsg = {
      key: `u-${Date.now()}`,
      role: 'user',
      content: text + (refs.length ? `\n\n（引用了 ${refs.map((r) => `@${r.label}`).join('、')}）` : ''),
      images,
      attachments,
    }
    setMessages((m) => [...m, userMsg])
    await runChat({ text, images, contextRefs: refs, attachments })
  }
  /** Plan 工作流：调用规划生成 API，把规划文档渲染为卡片消息（status=draft 待确认） */
  const runPlan = async (requirement: string) => {
    let sessionId = activeId
    if (!sessionId) {
      const s = await api.post<AgentSession>('/agent/sessions', {})
      sessionId = s.id
      setActiveId(s.id)
      qc.invalidateQueries({ queryKey: ['agent-sessions'] })
    }
    try {
      const plan = await api.post<AgentPlan>('/agent/plans', {
        session_id: sessionId,
        message: requirement,
        model_id: modelId || null,
      })
      setMessages((m) => [
        ...m,
        { key: `u-${Date.now()}`, role: 'user', content: requirement || '/plan' },
        { key: `plan-${plan.id}`, role: 'assistant', content: '', plan },
      ])
    } catch (e) {
      toast.error((e as Error).message || '规划生成失败')
    }
  }

  /** 确认规划：status draft → confirmed */
  const confirmPlan = async (planId: string) => {
    try {
      const plan = await api.post<AgentPlan>(`/agent/plans/${planId}/confirm`)
      setMessages((m) => m.map((x) => (x.plan?.id === planId ? { ...x, plan } : x)))
      toast.success('规划已确认，可逐步执行')
    } catch (e) {
      toast.error((e as Error).message || '确认失败')
    }
  }

  /** 执行规划步骤：以步骤描述作为用户消息发给模型（可带工具），完成后标记该步 done */
  const runPlanStep = async (plan: AgentPlan, stepIndex: number) => {
    const step = plan.steps?.[stepIndex]
    if (!step || sending) return
    const userMsg: LocalMsg = { key: `u-${Date.now()}`, role: 'user', content: step.description }
    setMessages((m) => [...m, userMsg])
    await runChat({ text: step.description })
    try {
      const updated = await api.post<AgentPlan>(`/agent/plans/${plan.id}/steps/${stepIndex}/done`)
      setMessages((m) => m.map((x) => (x.plan?.id === plan.id ? { ...x, plan: updated } : x)))
    } catch {
      /* 标记失败不阻断对话 */
    }
  }

  /** 提示词优化：调 optimize-prompt 接口，回填输入框 */
  const optimizeInput = async () => {
    if (!input.trim() || sending) return
    try {
      const { optimized } = await api.post<{ optimized: string }>('/agent/optimize-prompt', {
        message: input.trim(),
        model_id: modelId || null,
      })
      setInput(optimized)
      toast.success('已优化输入内容')
    } catch (e) {
      toast.error((e as Error).message || '优化失败')
    }
  }

  /** 创建会话副本（Fork）：复制到该条回复之前，新会话自动切入 */
  const forkSession = async (afterMessageId: string) => {
    if (!activeId || sending) return
    try {
      const s = await api.post<AgentSession>(
        `/agent/sessions/${activeId}/fork?after_message_id=${afterMessageId}`,
        {},
      )
      qc.invalidateQueries({ queryKey: ['agent-sessions'] })
      setActiveId(s.id)
      setMessages([])
      toast.success('已创建会话副本')
    } catch (e) {
      toast.error((e as Error).message || '创建副本失败')
    }
  }

  /** 手动压缩上下文：早期对话归档为摘要，释放上下文窗口 */
  const compactSession = async () => {
    if (!activeId) return
    try {
      const r = await api.post<{ compacted: boolean; message: string }>(
        `/agent/sessions/${activeId}/compact`,
        {},
      )
      toast.success(r.message || '已压缩')
      qc.invalidateQueries({ queryKey: ['agent-messages', activeId] })
    } catch (e) {
      toast.error((e as Error).message || '压缩失败')
    }
  }

  /** Goal 工作流：设定创作目标，渲染目标卡片（status=active，AI 多轮自动推进） */
  const runGoal = async (requirement: string) => {
    let sessionId = activeId
    if (!sessionId) {
      const s = await api.post<AgentSession>('/agent/sessions', {})
      sessionId = s.id
      setActiveId(s.id)
      qc.invalidateQueries({ queryKey: ['agent-sessions'] })
    }
    try {
      const goal = await api.post<AgentGoal>('/agent/goals', {
        session_id: sessionId,
        message: requirement,
        model_id: modelId || null,
      })
      setMessages((m) => [
        ...m,
        { key: `u-${Date.now()}`, role: 'user', content: requirement || '/goal' },
        { key: `goal-${goal.id}`, role: 'assistant', content: '', goal },
      ])
    } catch (e) {
      toast.error((e as Error).message || '目标设定失败')
    }
  }

  /** 长篇小说工作流：解析题材与章数 → 生成大纲预览卡片（等待用户确认） */
  const runNovel = async (requirement: string) => {
    if (sending) return
    // 解析章数：如「写30章」→ 30；其余文本为题材设定
    const m = requirement.match(/(\d{1,3})\s*章/)
    const chapters = m ? Math.min(100, Math.max(1, parseInt(m[1], 10))) : 10
    const brief = requirement.replace(/(\d{1,3})\s*章/g, '').trim() || requirement
    setSending(true)
    try {
      const outline = await api.post<NovelOutline>('/agent/novel-outline', {
        brief,
        genre: null,
        chapters,
        model_id: modelId || null,
      })
      setMessages((m) => [
        ...m,
        { key: `u-${Date.now()}`, role: 'user', content: requirement || '/novel' },
        { key: `novel-${Date.now()}`, role: 'assistant', content: '', novelOutline: outline },
      ])
    } catch (e) {
      toast.error((e as Error).message || '大纲生成失败')
    } finally {
      setSending(false)
    }
  }

  /** 确认大纲：创建小说 + 提交后台逐章写作任务，卡片切换为进度轮询 */
  const startNovelWriting = async (outline: NovelOutline) => {
    if (sending) return
    setSending(true)
    try {
      const r = await api.post<NovelWriteResp>('/agent/novel-write', {
        title: outline.title,
        brief: outline.logline,
        genre: outline.genre,
        chapters: outline.chapters.length,
        outline,
        model_id: modelId || null,
      })
      setMessages((m) =>
        m.map((x) =>
          x.novelOutline === outline
            ? {
                ...x,
                novelOutline: undefined,
                novelWriting: {
                  novelId: r.novel_id,
                  taskId: r.task_id,
                  title: r.title,
                  chapters: outline.chapters.length,
                },
              }
            : x,
        ),
      )
      toast.success('写作任务已提交，后台逐章生成中')
    } catch (e) {
      toast.error((e as Error).message || '提交写作任务失败')
    } finally {
      setSending(false)
    }
  }

  /** Goal 推进一轮：SSE 流式对话，结束后自动 evaluate 自评并刷新卡片 */
  const advanceGoal = async (goal: AgentGoal) => {
    if (sending) return
    const asstMsg: LocalMsg = {
      key: `a-${Date.now()}`,
      role: 'assistant',
      content: '',
      streaming: true,
    }
    setMessages((m) => [...m, asstMsg])
    setSending(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const res = await fetch(`/api/agent/goals/${goal.id}/advance`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ model_id: modelId || null }),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        const j = await res.json().catch(() => ({}))
        throw new Error(j.message || j.detail || `HTTP ${res.status}`)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''
        for (const part of parts) {
          const line = part.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let ev: AgentSSEEvent
          try {
            ev = JSON.parse(line.slice(6))
          } catch {
            continue
          }
          handleEvent(ev, (fn) => setMessages((prev) => fn(prev)))
        }
      }
      applyStreamingEnd(asstMsg)
      // 自评：更新目标状态与进度
      try {
        const updated = await api.post<AgentGoal>(`/agent/goals/${goal.id}/evaluate`, {
          model_id: modelId || null,
        })
        setMessages((m) => m.map((x) => (x.goal?.id === goal.id ? { ...x, goal: updated } : x)))
      } catch {
        /* 自评失败不影响对话 */
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        applyStreamingEnd(asstMsg)
        return
      }
      toast.error((e as Error).message || '目标推进失败')
      setMessages((m) =>
        m.map((x) => (x.key === asstMsg.key ? { ...x, streaming: false, error: (e as Error).message } : x)),
      )
    } finally {
      setSending(false)
    }
  }

  /** 更新目标状态（暂停/恢复/标记完成） */
  const setGoalStatus = async (goal: AgentGoal, status: string) => {
    try {
      const updated = await api.post<AgentGoal>(`/agent/goals/${goal.id}/status`, { status })
      setMessages((m) => m.map((x) => (x.goal?.id === goal.id ? { ...x, goal: updated } : x)))
    } catch (e) {
      toast.error((e as Error).message || '操作失败')
    }
  }

  /** P8 Phase 5：上传单个非图片附件 → POST /agent/attachments（base64）→ 加入待发送列表 */
  const uploadAttachment = async (file: File) => {
    const reader = new FileReader()
    const dataBase64 = await new Promise<string>((resolve, reject) => {
      reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
    const r = await api.post<{ ok: boolean; attachment: AgentAttachment }>(
      `/agent/attachments${activeId ? `?session_id=${activeId}` : ''}`,
      { filename: file.name, data_base64: dataBase64 },
    )
    setPendingAttachments((prev) => [...prev, r.attachment])
  }

  /**
   * 统一文件入口（文件选择 / 拖拽 / 剪贴板粘贴共用）：
   * 图片 → 原 data URI 预览（视觉理解 + 生图参考）；音频/视频/文档 → 附件上传（转写/抽帧/提取文本）
   */
  const handleFiles = async (files: FileList | File[] | null) => {
    if (!files || files.length === 0 || sending) return
    const arr = Array.from(files).slice(0, 6)
    const images = arr.filter((f) => f.type.startsWith('image/'))
    const others = arr.filter((f) => !f.type.startsWith('image/'))
    if (images.length) pickImages(images as unknown as FileList)
    if (others.length) {
      setUploading(true)
      try {
        for (const f of others) {
          try {
            await uploadAttachment(f)
          } catch (e) {
            toast.error(`${f.name}：${(e as Error).message || '上传失败'}`)
          }
        }
      } finally {
        setUploading(false)
      }
    }
  }

  /** 选择本地图片 → data URI（视觉理解 + 按图生图参考） */
  const pickImages = (files: FileList | null) => {
    if (!files) return
    const arr = Array.from(files).slice(0, 9)
    for (const f of arr) {
      if (!f.type.startsWith('image/')) continue
      const reader = new FileReader()
      reader.onload = () => {
        setPendingImages((prev) =>
          prev.length >= 9 ? prev : [...prev, reader.result as string],
        )
      }
      reader.readAsDataURL(f)
    }
  }

  /** 移除待发送附件 */
  const removeAttachment = (index: number) => {
    setPendingAttachments((prev) => prev.filter((_, i) => i !== index))
  }

  /** @ 引用上下文：加入待发送 refs 列表（项目/文档/会话/资产），输入框标注 @标签 */
  const addContextRef = (ref: ContextRef) => {
    setPendingRefs((prev) =>
      prev.some((r) => r.type === ref.type && r.id === ref.id) ? prev : [...prev, ref],
    )
    setInput((prev) => `${prev.replace(/\s*$/, '')} @${ref.label} `.trimStart())
    setAtPickerOpen(false)
  }

  /** 移除待发送的上下文引用 */
  const removeContextRef = (index: number) => {
    setPendingRefs((prev) => prev.filter((_, i) => i !== index))
  }

  /** 语音输入：浏览器 Web Speech API（识别模型由浏览器提供，后续可替换为独立识别服务） */
  const toggleVoice = () => {
    const win = window as unknown as Record<string, unknown>
    const SR = (win.SpeechRecognition || win.webkitSpeechRecognition) as
      | (new () => {
          lang: string
          interimResults: boolean
          continuous: boolean
          onresult: (e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void
          onend: () => void
          onerror: () => void
          start: () => void
          stop: () => void
        })
      | undefined
    if (!SR) {
      toast.error('当前浏览器不支持语音输入（推荐 Chrome）')
      return
    }
    if (listening) {
      ;(voiceRecRef.current as { stop?: () => void } | null)?.stop?.()
      return
    }
    const rec = new SR()
    rec.lang = 'zh-CN'
    rec.interimResults = true
    rec.continuous = false
    rec.onresult = (e) => {
      let text = ''
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript
      setInput((prev) => (prev ? prev + text : text))
    }
    rec.onend = () => setListening(false)
    rec.onerror = () => setListening(false)
    voiceRecRef.current = rec
    rec.start()
    setListening(true)
  }

  /** 按谓词就地更新工具状态（同时覆盖独立 tool 行与助手消息内嵌工具块） */
  const patchToolStates = <
    T extends {
      key?: string
      toolName?: string
      toolStatus?: string
      toolParams?: Record<string, unknown>
      content?: string
      projectId?: string
      mediaUrls?: string[]
    },
  >(
    msgs: LocalMsg[],
    pred: (t: T) => boolean,
    patch: (t: T) => T,
  ): LocalMsg[] =>
    msgs.map((x) => {
      const hitRow = x.role === 'tool' && pred(x as unknown as T)
      const parts = x.parts
      if (!parts || parts.length === 0) return hitRow ? (patch(x as unknown as T) as unknown as LocalMsg) : x
      let changed = false
      const next = parts.map((p) => {
        if (p.kind === 'tool' && pred(p as unknown as T)) {
          changed = true
          return { ...p, ...(patch(p as unknown as T) as unknown as AssistantPart) }
        }
        return p
      })
      if (changed) return { ...x, parts: next }
      return hitRow ? (patch(x as unknown as T) as unknown as LocalMsg) : x
    })

  /** 停止当前生成：中断流并清除 streaming 标记（保留已输出的内容），并把本轮工具卡标记为中断 */
  const markToolsInterrupted = () =>
    setMessages((m) =>
      patchToolStates(
        m,
        (t) => t.toolStatus === 'running',
        (t) => ({ ...t, toolStatus: 'failed', content: t.content || '已中断' }),
      ),
    )

  const stop = () => {
    abortRef.current?.abort()
    setMessages((m) => m.map((x) => (x.streaming ? { ...x, streaming: false } : x)))
    markToolsInterrupted()
  }

  /** 流结束：清除某条 assistant 消息的 streaming 标记 */
  const applyStreamingEnd = (asstMsg: LocalMsg) => {
    setMessages((m) => m.map((x) => (x.key === asstMsg.key ? { ...x, streaming: false } : x)))
  }

  /** 重新生成：截断到目标 assistant 消息之前，按原问题重新请求 */
  const regenerate = async (msgKey: string) => {
    if (sending) return
    const idx = messages.findIndex((m) => m.key === msgKey)
    const target = messages[idx]
    if (idx < 0 || !target.dbId) return
    setMessages(messages.slice(0, idx))
    await runChat({ regenerateMessageId: target.dbId })
  }

  /** 复制消息内容到剪贴板 */
  const copyMessage = async (content: string) => {
    try {
      await navigator.clipboard.writeText(content)
      toast.success('已复制到剪贴板')
    } catch {
      toast.error('复制失败')
    }
  }

  /** P8 Phase 4：朗读消息内容（TTS 合成 → 播放音频） */
  const speakMessage = async (content: string) => {
    if (!ttsReady) {
      toast.error(ttsReason || '语音合成服务不可用')
      return
    }
    try {
      const r = await api.post<{ ok: boolean; audio_url: string }>('/agent/tts', {
        text: content.slice(0, 500),
      })
      const audio = new Audio(r.audio_url)
      audio.play()
    } catch (e) {
      toast.error((e as Error).message || '语音合成失败')
    }
  }

  /** P8：确认 git 提交（模型调用 git_commit 后前端二次确认，真正执行） */
  const confirmGitCommit = async (info?: { files?: string[]; message?: string }) => {
    if (!info || !info.message) return
    try {
      const r = await api.post<{ ok: boolean; message: string }>('/agent/git/commit', {
        files: info.files ?? [],
        message: info.message,
      })
      toast.success('已提交')
      setMessages((m) =>
        patchToolStates(
          m,
          (t) => t.toolName === 'git_commit' && t.toolStatus === 'pending',
          (t) => ({ ...t, toolStatus: 'succeeded', content: r.message }),
        ),
      )
    } catch (e) {
      toast.error((e as Error).message || '提交失败')
    }
  }

  /** 确认创建项目：按用户在确认卡上选择的画幅/风格/视频参数，把草案落库为完整项目（含资产+分镜） */
  const confirmProject = async (
    msg: LocalMsg,
    opts: ConfirmProjectOpts,
  ) => {
    if (!msg.dbId) {
      toast.error('项目草案不存在，请重新让智能体生成项目')
      return
    }
    try {
      const r = await api.post<{ project_id: string; title: string }>('/agent/projects/confirm', {
        message_id: msg.dbId,
        aspect_ratio: opts.aspect_ratio,
        style_id: opts.style_id,
        art_style_prompt: opts.art_style_prompt,
        resolution: opts.resolution ?? '720p',
        video_params: opts.video_params ?? {},
      })
      toast.success(`项目「${r.title}」已创建`)
      // 更新内嵌工具块（live 时为助手消息 parts 内的 create_project 卡）
      setMessages((m) =>
        patchToolStates(
          m,
          (t) => t.key === msg.key || (t.toolName === 'create_project' && t.toolStatus === 'pending'),
          (t) => ({
            ...t,
            toolStatus: 'succeeded',
            projectId: r.project_id,
            content: `项目「${r.title}」已创建`,
          }),
        ),
      )
      qc.invalidateQueries({ queryKey: ['projects'] })
    } catch (e) {
      toast.error((e as Error).message || '项目创建失败')
    }
  }

  /** 确认删除项目：用户点击确认卡后真正删除（含幕/分镜/资产/视频/音频及磁盘文件） */
  const confirmProjectDelete = async (msg: LocalMsg) => {
    if (!msg.dbId) {
      toast.error('项目删除确认不存在，请重新让智能体选择项目删除')
      return
    }
    try {
      const r = await api.post<{ title: string; detail: string }>('/agent/projects/delete', {
        message_id: msg.dbId,
      })
      toast.success(`项目「${r.title}」已删除`)
      setMessages((m) =>
        patchToolStates(
          m,
          (t) => t.key === msg.key || (t.toolName === 'project_delete' && t.toolStatus === 'pending'),
          (t) => ({
            ...t,
            toolStatus: 'succeeded',
            toolParams: { ...(t.toolParams ?? {}), project_deleted: true },
            content: `项目「${r.title}」已删除`,
          }),
        ),
      )
      qc.invalidateQueries({ queryKey: ['projects'] })
    } catch (e) {
      toast.error((e as Error).message || '项目删除失败')
    }
  }

  /** P9：确认 git 推送（模型调用 git_push 后前端二次确认，真正执行） */
  const confirmGitPush = async (info?: { remote?: string; branch?: string }) => {
    if (!info) return
    try {
      const r = await api.post<{ ok: boolean; message: string }>('/agent/git/push', {
        remote: info.remote ?? 'origin',
        branch: info.branch ?? '',
      })
      toast.success('已推送')
      setMessages((m) =>
        patchToolStates(
          m,
          (t) => t.toolName === 'git_push' && t.toolStatus === 'pending',
          (t) => ({ ...t, toolStatus: 'succeeded', content: r.message }),
        ),
      )
    } catch (e) {
      toast.error((e as Error).message || '推送失败')
    }
  }

  /** P9：保存会话级工具白名单（null=全部工具可用；数组=仅白名单内可用） */
  const saveToolWhitelist = async (whitelist: string[] | null) => {
    if (!activeId) return
    try {
      await api.put(`/agent/sessions/${activeId}`, { tool_whitelist: whitelist })
      qc.invalidateQueries({ queryKey: ['agent-sessions'] })
      toast.success(whitelist ? '已更新工具白名单' : '已恢复为全部工具可用')
      setToolPermOpen(false)
    } catch (e) {
      toast.error((e as Error).message || '保存失败')
    }
  }

  /** 统一发起对话（普通发送 / 重新生成共用） */
  const runChat = async (opts: {
    text?: string
    images?: string[]
    regenerateMessageId?: string
    contextRefs?: ContextRef[]
    attachments?: AgentAttachment[]
  }) => {
    let sessionId = activeId
    if (!sessionId) {
      const s = await api.post<AgentSession>('/agent/sessions', {})
      sessionId = s.id
      setActiveId(s.id)
      qc.invalidateQueries({ queryKey: ['agent-sessions'] })
    }
    const asstMsg: LocalMsg = { key: `a-${Date.now()}`, role: 'assistant', content: '', streaming: true }
    setMessages((m) => [...m, asstMsg])
    setSending(true)

    const controller = new AbortController()
    abortRef.current = controller
    const apply = (fn: (m: LocalMsg[]) => LocalMsg[]) => setMessages((prev) => fn(prev))
    // 超时保护（防卡死）：SSE 长时间无任何事件到达（空闲超时）或总时长超限时，
    // 自动终止并提示，避免「后端工具链过长/模型持续思考/连接静默断开」时无限转圈。
    // 2026-08-16 修复：空闲 120s → 240s（工具执行期间 SSE 无输出，生图/多工具串行
    // 常超 2 分钟，被误判超时）；总时长 600s → 680s（后端总预算 600s 先发温和错误，
    // 前端不抢先 abort，用户看到的是后端「已自动停止，请拆分请求」而非连接中断）。
    // 2026-08-16b：新增「首字节超时」——fetch 发出后 15s 内后端未返回任何响应头
    //（连接半开/代理挂起/请求未送达）即显式报错，不再让消息无限挂在「思考中」。
    const IDLE_TIMEOUT_MS = 240_000 // 240 秒无事件
    const TOTAL_TIMEOUT_MS = 680_000 // 11 分钟总时长上限
    const HEADER_TIMEOUT_MS = 15_000 // 15 秒无响应头
    let timedOut = false
    let headerTimer: ReturnType<typeof setTimeout> | undefined
    let idleTimer: ReturnType<typeof setTimeout> | undefined
    let totalTimer: ReturnType<typeof setTimeout> | undefined
    const timeoutDone = (reason: 'header' | 'idle' | 'total' = 'idle') => {
      if (timedOut) return
      timedOut = true
      if (headerTimer) clearTimeout(headerTimer)
      if (idleTimer) clearTimeout(idleTimer)
      if (totalTimer) clearTimeout(totalTimer)
      controller.abort()
      const msg =
        reason === 'header'
          ? '请求未送达后端（15 秒无响应），请确认后端服务已启动后重试。'
          : reason === 'total'
            ? '处理时间过长，已自动停止。请拆分请求后重试。'
            : '响应超时，已自动停止。请重试或拆分请求。'
      apply((m) => m.map((x) => (x.key === asstMsg.key ? { ...x, streaming: false, error: msg } : x)))
      markToolsInterrupted()
      toast.error(msg)
      // 2026-08-16 自检诊断：header 超时时探测「同源最小请求 /api/health」，
      // 区分失败发生在「浏览器↔前端/代理」还是「后端端点」——把结果直接追加到气泡错误里，
      // 无需用户开 F12。
      if (reason === 'header') {
        fetch('/api/health', { headers: authHeaders(), signal: AbortSignal.timeout(4000) })
          .then((r) => {
            const diag = r.ok
              ? '同源 /api/health 返回 ' + r.status + '（前端与后端链路正常），问题出在 /api/agent/chat 端点或推理后端'
              : '同源 /api/health 返回 HTTP ' + r.status + '——请检查后端服务状态'
            apply((m) =>
              m.map((x) => (x.key === asstMsg.key ? { ...x, error: msg + '\n[诊断] ' + diag } : x)),
            )
          })
          .catch(() => {
            const diag = '同源 /api/health 也超时/失败——浏览器到后端整条链路不通（网络/代理/vite 监听栈）'
            apply((m) =>
              m.map((x) => (x.key === asstMsg.key ? { ...x, error: msg + '\n[诊断] ' + diag } : x)),
            )
          })
      }
    }
    const armIdleTimer = () => {
      if (timedOut) return
      if (idleTimer) clearTimeout(idleTimer)
      idleTimer = setTimeout(() => timeoutDone('idle'), IDLE_TIMEOUT_MS)
    }
    headerTimer = setTimeout(() => timeoutDone('header'), HEADER_TIMEOUT_MS)
    totalTimer = setTimeout(() => timeoutDone('total'), TOTAL_TIMEOUT_MS)
    armIdleTimer()
    try {
      const res = await fetch('/api/agent/chat', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          message: opts.text ?? '',
          session_id: sessionId,
          model_id: modelId || null,
          regenerate_message_id: opts.regenerateMessageId ?? null,
          images: opts.images ?? [],
          context_refs: (opts.contextRefs ?? []).map((r) => ({ type: r.type, id: r.id, label: r.label })),
          attachments: opts.attachments ?? [],
          plugins: activePlugins,
        }),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        const j = await res.json().catch(() => ({}))
        throw new Error(j.message || j.detail || `HTTP ${res.status}`)
      }
      // 已收到响应头 → 首字节超时解除
      if (headerTimer) clearTimeout(headerTimer)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''
        for (const part of parts) {
          const line = part.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let ev: AgentSSEEvent
          try {
            ev = JSON.parse(line.slice(6))
          } catch {
            continue
          }
          armIdleTimer() // 收到事件即重置空闲计时
          handleEvent(ev, apply)
        }
      }
      // 流结束：标记 assistant 完成（思考过程随之结束，可收起折叠区）
      apply((m) =>
        m.map((x) => (x.key === asstMsg.key ? { ...x, streaming: false, thinkingDone: true } : x)),
      )
      qc.invalidateQueries({ queryKey: ['agent-sessions'] })
      qc.invalidateQueries({ queryKey: ['agent-messages', sessionId] })
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        // 超时自动终止：已在上方处理并提示；用户主动停止：保留已生成内容，仅结束流
        if (!timedOut) {
          apply((m) =>
            m.map((x) =>
              x.key === asstMsg.key ? { ...x, streaming: false, thinkingDone: true } : x,
            ),
          )
        }
        return
      }
      // 非中止错误：网络波动/服务端异常。若已生成了部分内容保留温和收尾；
      // 完全无内容才提示，且把原始网络错误转成友好中文，不显示「Failed to fetch」。
      // 无论哪种情况都立即失效服务端消息缓存——后端有断连兜底落库/后台继续执行，
      // 切回会话/稍后由轮询从服务端恢复完整内容（避免误报「连接中断」）。
      apply((m) => {
        const target = m.find((x) => x.key === asstMsg.key)
        const partial = (target?.content?.trim() || target?.thinking?.trim()) ? true : false
        const rawMsg = (e as Error).message || ''
        const friendly =
          /failed to fetch|networkerror|load failed|etimedout|network request failed/i.test(rawMsg)
            ? '网络连接波动，本次回复已中断。正在从服务端恢复已生成的内容，请稍候…'
            : rawMsg || '发送失败'
        if (!partial) toast.error(friendly)
        return m.map((x) =>
          x.key === asstMsg.key
            ? {
                ...x,
                streaming: false,
                thinkingDone: true,
                error: partial ? undefined : friendly,
              }
            : x,
        )
      })
      qc.invalidateQueries({ queryKey: ['agent-messages', sessionId] })
    } finally {
      if (headerTimer) clearTimeout(headerTimer)
      if (idleTimer) clearTimeout(idleTimer)
      if (totalTimer) clearTimeout(totalTimer)
      setSending(false)
    }
  }

  const handleEvent = (ev: AgentSSEEvent, apply: (fn: (m: LocalMsg[]) => LocalMsg[]) => void) => {
    switch (ev.type) {
      case 'token':
        // 正文增量：追加到流式助手消息的「最后一个文本块」内（若已在工具块之后，
        // 则新建文本块，保证与工具卡按发生顺序交错，1:1 对齐 dsh web）
        apply((m) =>
          m.map((x) => {
            if (!x.streaming) return x
            const delta = ev.content ?? ''
            if (!delta) return x
            const parts = x.parts ? [...x.parts] : []
            const last = parts[parts.length - 1]
            if (last && last.kind === 'text') {
              parts[parts.length - 1] = { ...last, text: (last.text ?? '') + delta }
            } else {
              parts.push({ kind: 'text', key: `pt-${x.key}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, text: delta })
            }
            return { ...x, parts, content: (x.content ?? '') + delta }
          }),
        )
        break
      case 'thinking':
        // 推理模型思考过程（reasoning_content）：累积到当前 streaming 助手消息，
        // 渲染为可折叠「思考过程」区（仅本地展示，不入库）。
        // 思考阶段完成（工具开始执行）时置 thinkingDone 收起折叠区；工具后又出现
        // 新思考（如拿到结果继续推理）则重新展开——与 dsh web 的思考块行为一致。
        apply((m) =>
          m.map((x) => {
            if (!x.streaming) return x
            const hasToolParts = (x.parts ?? []).some((p) => p.kind === 'tool')
            return {
              ...x,
              thinking: (x.thinking ?? '') + (ev.content ?? ''),
              thinkingDone: hasToolParts ? false : x.thinkingDone,
            }
          }),
        )
        break
      case 'tool': {
        // 事件携带的待确认/异步任务/并行标记参数（新建卡片时写入；更新时若事件无参数则保留原卡）
        const toolParams = ev.pending_project_draft
          ? { pending_project_draft: ev.pending_project_draft }
          : ev.pending_project_delete
            ? { pending_project_delete: ev.pending_project_delete }
            : ev.pending_git_commit
              ? { pending_git_commit: ev.pending_git_commit }
              : ev.pending_git_push
                ? { pending_git_push: ev.pending_git_push }
                : ev.draft_id
                  ? { draft_id: ev.draft_id, task_id: ev.task_id }
                  : ev.task_id
                    ? { task_id: ev.task_id, novel_id: ev.novel_id }
                    : ev.parallel
                      ? { parallel: true }
                      : undefined
        apply((m) => {
          const copy = [...m]
          // 同工具已有未完成（running/pending/failed）卡片 → 更新；否则新建。
          // 1:1 对齐 dsh web：工具卡作为「时序块」渲染在流式助手消息内部
          // （位于其发生时刻——先于工具的前置正文之后、最终汇报正文之前）。
          // 历史回放的独立 tool 行（旧数据兜底）也兼容更新。
          for (let i = copy.length - 1; i >= 0; i--) {
            const x = copy[i]
            if (!x.streaming) continue
            // ① 更新助手消息内已有的同工具未完成块
            const parts = x.parts ? [...x.parts] : []
            // 结果内容去重（防「一句话冒出 4 张一样的结果卡」）：同名工具已存在
            // 相同结果的卡时，把其余同工具卡（同结果/未完成的重复重试）合并掉，
            // 只保留一张 —— 1:1 对齐 dsh web「一次搜索一张卡、正文为主」的观感。
            if (ev.status === 'succeeded' && ev.message) {
              const contentHit = parts.findIndex(
                (q) => q.kind === 'tool' && q.toolName === ev.name && q.content === ev.message,
              )
              if (contentHit >= 0) {
                const merged: AssistantPart[] = []
                parts.forEach((q, idx) => {
                  const sameTool = q.kind === 'tool' && q.toolName === ev.name
                  if (idx === contentHit) {
                    merged.push({ ...q, toolStatus: ev.status })
                    return
                  }
                  if (sameTool && (q.content === ev.message || q.toolStatus === 'running' || q.toolStatus === 'pending')) {
                    return // 合并：同结果卡 / 未完成的重复重试卡
                  }
                  merged.push(q)
                })
                copy[i] = { ...x, parts: merged, thinkingDone: x.thinkingDone }
                return copy
              }
            }
            let updated = false
            for (let j = parts.length - 1; j >= 0; j--) {
              const p = parts[j]
              if (p.kind === 'tool' && p.toolName === ev.name && p.toolStatus !== 'succeeded') {
                parts[j] = {
                  ...p,
                  toolStatus: ev.status ?? p.toolStatus,
                  toolStep: ev.step ?? p.toolStep,
                  content:
                    ev.status === 'failed'
                      ? (ev.message as string)
                      : ev.status === 'succeeded' && ev.message
                        ? (ev.message as string)
                        : p.content,
                  toolParams: toolParams ?? p.toolParams,
                }
                updated = true
                break
              }
            }
            if (!updated) {
              // ② 新建工具块：追加到当前文本块之后（保持时间顺序）
              parts.push({
                kind: 'tool',
                key: `ptool-${x.key}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                toolName: ev.name,
                toolStatus: ev.status ?? 'running',
                toolStep: ev.step,
                content:
                  ev.status === 'failed'
                    ? (ev.message ?? '')
                    : ev.status === 'succeeded'
                      ? (ev.message ?? '')
                      : '',
                toolParams,
              })
            }
            // 工具开始 = 本轮思考阶段结束：收起「思考过程」折叠区（避免思考/工具/正文同屏闪烁）
            copy[i] = { ...x, parts, thinkingDone: ev.status === 'running' ? true : x.thinkingDone }
            return copy
          }
          // ③ 兜底：无 streaming 助手消息时退回独立工具行（旧历史/非流式路径）
          for (let i = copy.length - 1; i >= 0; i--) {
            const x = copy[i]
            if (x.role === 'tool' && x.toolName === ev.name && x.toolStatus !== 'succeeded') {
              copy[i] = {
                ...x,
                toolStatus: ev.status ?? x.toolStatus,
                toolStep: ev.step ?? x.toolStep,
                content:
                  ev.status === 'failed'
                    ? (ev.message as string)
                    : ev.status === 'succeeded' && ev.message
                      ? (ev.message as string)
                      : x.content,
                toolParams: toolParams ?? x.toolParams,
              }
              return copy
            }
          }
          copy.push({
            key: `t-${Date.now()}-${ev.name}-${Math.random().toString(36).slice(2, 6)}`,
            role: 'tool',
            content: ev.status === 'failed' ? (ev.message ?? '') : ev.status === 'succeeded' ? (ev.message ?? '') : '',
            toolName: ev.name,
            toolStatus: ev.status ?? 'running',
            toolStep: ev.step,
            toolParams,
          })
          return copy
        })
        break
      }
      case 'media':
        apply((m) => {
          const copy = [...m]
          const targetName = (ev as { name?: string }).name
          // ① 优先更新流式助手消息内的工具块（generate_image 等生图卡）
          for (let i = copy.length - 1; i >= 0; i--) {
            const x = copy[i]
            if (!x.streaming || !x.parts?.length) continue
            const parts = [...x.parts]
            for (let j = parts.length - 1; j >= 0; j--) {
              const p = parts[j]
              if (p.kind !== 'tool' || p.toolStatus === 'succeeded') continue
              const matched =
                targetName ? p.toolName === targetName : p.toolName === 'generate_image'
              if (!matched) continue
              const url = ev.url ?? ''
              parts[j] = {
                ...p,
                toolStatus: 'succeeded',
                mediaUrls:
                  url && !(p.mediaUrls ?? []).includes(url)
                    ? [...(p.mediaUrls ?? []), url]
                    : (p.mediaUrls ?? []),
              }
              copy[i] = { ...x, parts }
              return copy
            }
          }
          // ② 兜底：独立工具行（历史/旧路径）
          for (let i = copy.length - 1; i >= 0; i--) {
            const x = copy[i]
            // 优先匹配事件指定工具卡（如 browser_screenshot），否则回退生成图片卡
            const matched =
              x.role === 'tool' &&
              x.toolStatus !== 'succeeded' &&
              (targetName ? x.toolName === targetName : x.toolName === 'generate_image')
            if (matched) {
              const url = ev.url ?? ''
              copy[i] = {
                ...x,
                toolStatus: 'succeeded',
                // 去重：同一 URL 的重复 media 事件不重复追加
                mediaUrls: url && !(x.mediaUrls ?? []).includes(url) ? [...(x.mediaUrls ?? []), url] : x.mediaUrls ?? [],
              }
              break
            }
          }
          return copy
        })
        break
      case 'project':
        apply((m) => {
          const copy = [...m]
          // ① 优先更新流式助手消息内的 create_project 工具块
          for (let i = copy.length - 1; i >= 0; i--) {
            const x = copy[i]
            if (!x.streaming || !x.parts?.length) continue
            const parts = [...x.parts]
            for (let j = parts.length - 1; j >= 0; j--) {
              const p = parts[j]
              if (p.kind === 'tool' && p.toolName === 'create_project' && p.toolStatus !== 'succeeded') {
                parts[j] = { ...p, toolStatus: 'succeeded', projectId: ev.id }
                copy[i] = { ...x, parts }
                return copy
              }
            }
          }
          // ② 兜底：独立工具行（历史/旧路径）
          for (let i = copy.length - 1; i >= 0; i--) {
            const x = copy[i]
            if (x.role === 'tool' && x.toolName === 'create_project') {
              copy[i] = { ...x, toolStatus: 'succeeded', projectId: ev.id }
              break
            }
          }
          return copy
        })
        break
      case 'done':
        // 记录 assistant 消息数据库 id（供重新生成使用）
        if (ev.message_id) {
          apply((m) =>
            m.map((x) => (x.streaming ? { ...x, dbId: ev.message_id } : x)),
          )
        }
        if (typeof ev.context_tokens === 'number') {
          setCtxTokens(ev.context_tokens)
        }
        break
      case 'error':
        apply((m) => {
          const copy = [...m]
          for (let i = copy.length - 1; i >= 0; i--) {
            if (copy[i].streaming) {
              copy[i] = { ...copy[i], streaming: false, error: ev.message ?? '出错了' }
              break
            }
          }
          return copy
        })
        break
    }
  }

  return (
    <div className="h-[calc(100vh-56px)] flex max-w-[1600px] mx-auto">
      {/* 左栏：会话列表 */}
      <aside className="w-60 shrink-0 border-r border-slate-200 flex flex-col bg-white/60">
        <div className="p-3 space-y-2">
          <Button className="w-full" size="sm" onClick={newSession} leftIcon={<Icon name="plus" size={14} />}>
            新建对话
          </Button>
          <button
            type="button"
            onClick={exportSession}
            disabled={messages.length === 0}
            className="w-full flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg py-1.5 text-slate-500 hover:text-brand-600 hover:bg-slate-100 disabled:opacity-40 transition-colors"
          >
            <Icon name="download" size={13} />
            导出会话
          </button>
        </div>
        {/* P8 Phase 3 会话历史搜索框 */}
        <div className="px-2 pb-1.5">
          <div className="relative">
            <Icon name="search" size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索历史对话…"
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
        <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
          {searchQuery.trim() ? (
            searchResults.length === 0 ? (
              <p className="text-xs text-slate-400 text-center mt-6">无匹配结果</p>
            ) : (
              searchResults.map((h) => (
                <button
                  key={h.message_id}
                  type="button"
                  onClick={() => openSearchHit(h)}
                  className="w-full text-left rounded-lg px-2.5 py-2 text-[13px] hover:bg-brand-50/50 transition-colors group"
                >
                  <span className="flex items-center gap-1.5">
                    <span className={cn(
                      'text-[10px] font-medium rounded-full px-1.5 py-px shrink-0',
                      h.role === 'user' ? 'bg-slate-200 text-slate-600' : 'bg-brand-100 text-brand-600',
                    )}>
                      {h.role === 'user' ? '我' : '助手'}
                    </span>
                    <span className="flex-1 truncate text-slate-600 group-hover:text-brand-700 text-xs">{h.session_title}</span>
                    <Icon name="chevron-right" size={11} className="text-slate-300 shrink-0" />
                  </span>
                  <span className="block text-[11px] text-slate-400 leading-relaxed mt-1 line-clamp-2">{h.snippet}</span>
                </button>
              ))
            )
          ) : (
            <>
            {sessions.map((s) => (
            <div
              key={s.id}
              className={cn(
                'group flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-[13px] cursor-pointer transition-colors',
                s.id === activeId
                  ? 'bg-brand-500/10 text-brand-700'
                  : 'text-slate-600 hover:bg-slate-100',
              )}
              onClick={() => switchSession(s.id)}
            >
              <Icon name="clapperboard" size={13} className="shrink-0 opacity-60" />
              <span className="flex-1 truncate">{s.title}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  deleteSession(s.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-slate-400 hover:text-rose-500"
                title="删除会话"
              >
                <Icon name="trash" size={12} />
              </button>
            </div>
            ))}
            {sessions.length === 0 && (
              <p className="text-xs text-slate-400 text-center mt-6">暂无会话</p>
            )}
            </>
          )}
        </div>
        {/* 左栏底部：记忆 + 技能管理等（角色功能已删除，仅保留通用智能体） */}
        <div className="p-2 border-t border-slate-100 space-y-0.5">
          <button
            type="button"
            onClick={() => setMemoryOpen(true)}
            className="w-full flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg py-1.5 text-slate-500 hover:text-brand-600 hover:bg-slate-100 transition-colors"
          >
            <Icon name="brain" size={13} />
            记忆管理
          </button>
          <button
            type="button"
            onClick={() => setSkillOpen(true)}
            className="w-full flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg py-1.5 text-slate-500 hover:text-brand-600 hover:bg-slate-100 transition-colors"
          >
            <Icon name="zap" size={13} />
            Skill
          </button>
          <button
            type="button"
            onClick={() => setMcpOpen(true)}
            className="w-full flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg py-1.5 text-slate-500 hover:text-brand-600 hover:bg-slate-100 transition-colors"
          >
            <Icon name="package" size={13} />
            MCP
          </button>
          <button
            type="button"
            onClick={() => setRuleOpen(true)}
            className="w-full flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg py-1.5 text-slate-500 hover:text-brand-600 hover:bg-slate-100 transition-colors"
          >
            <Icon name="list" size={13} />
            规则
          </button>
        </div>
      </aside>

      {/* 右栏：聊天区 */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 顶栏：工作台开关（T6） */}
        <div className="flex items-center justify-between px-5 py-2 border-b border-slate-100">
          <span className="text-xs text-slate-400">
            {activeId ? '创作对话' : '创作助手'}
          </span>
          <div className="flex items-center gap-1">
            {/* P9 会话级工具权限（仅选中会话可配置） */}
            {activeId && (
              <button
                type="button"
                onClick={() => setToolPermOpen(true)}
                className={cn(
                  'flex items-center gap-1.5 text-xs font-medium rounded-lg px-2.5 py-1.5 transition-colors',
                  currentWhitelist
                    ? 'text-amber-600 bg-amber-50 hover:bg-amber-100'
                    : 'text-slate-500 hover:text-brand-600 hover:bg-slate-100',
                )}
                title={currentWhitelist ? `工具白名单：${currentWhitelist.length} 个工具` : '工具权限：全部工具可用'}
              >
                <Icon name="shield" size={13} />
                {currentWhitelist ? `白名单 ${currentWhitelist.length}` : '全部工具'}
              </button>
            )}
            <button
              type="button"
              onClick={() => setWorkbenchOpen((v) => !v)}
              className={cn(
                'flex items-center gap-1.5 text-xs font-medium rounded-lg px-2.5 py-1.5 transition-colors',
                workbenchOpen
                  ? 'text-brand-600 bg-brand-50'
                  : 'text-slate-500 hover:text-brand-600 hover:bg-slate-100',
              )}
            >
              <Icon name="grid" size={13} />
              创作工作台
            </button>
          </div>
        </div>
        {/* 消息流（Trae Work 风格：居中列式布局） */}
        <div ref={listRef} className="flex-1 overflow-y-auto px-4 py-6">
          <div className="max-w-3xl mx-auto space-y-6">
          {messages.length === 0 && (
            <WelcomePanel onPick={(t) => { setInput(t); setSending(false) }} />
          )}
          {messages.map((m) => (
            <div id={`msg-${m.key}`} key={m.key}>
            <MessageBubble
              key={m.key}
              msg={m}
              onCopy={copyMessage}
              onSaveImage={setSaveTarget}
              onSpeak={speakMessage}
              ttsReady={ttsReady}
              ttsReason={ttsReason}
              highlightKw={m.role === 'assistant' && m.dbId === focusMsgId ? highlightKw : undefined}
              onRegenerate={m.role === 'assistant' && !m.streaming && !!m.dbId ? regenerate : undefined}
              onConfirmPlan={m.plan ? () => confirmPlan(m.plan!.id) : undefined}
              onRunPlanStep={m.plan ? (idx) => runPlanStep(m.plan!, idx) : undefined}
              onAdvanceGoal={m.goal ? () => advanceGoal(m.goal!) : undefined}
              onSetGoalStatus={m.goal ? (s) => setGoalStatus(m.goal!, s) : undefined}
              onFork={m.role === 'assistant' && !!m.dbId ? () => forkSession(m.dbId!) : undefined}
              onStartNovel={m.novelOutline ? () => startNovelWriting(m.novelOutline!) : undefined}
              onConfirmGitCommit={
                m.toolName === 'git_commit' ||
                (m.role === 'assistant' && (m.parts ?? []).some((p) => p.kind === 'tool' && p.toolName === 'git_commit'))
                  ? confirmGitCommit
                  : undefined
              }
              onConfirmGitPush={
                m.toolName === 'git_push' ||
                (m.role === 'assistant' && (m.parts ?? []).some((p) => p.kind === 'tool' && p.toolName === 'git_push'))
                  ? confirmGitPush
                  : undefined
              }
              onConfirmProject={
                m.toolName === 'create_project' ||
                (m.role === 'assistant' && (m.parts ?? []).some((p) => p.kind === 'tool' && p.toolName === 'create_project'))
                  ? confirmProject
                  : undefined
              }
              onConfirmProjectDelete={
                m.toolName === 'project_delete' ||
                (m.role === 'assistant' && (m.parts ?? []).some((p) => p.kind === 'tool' && p.toolName === 'project_delete'))
                  ? confirmProjectDelete
                  : undefined
              }
            />
            </div>
          ))}
          {/* 上下文使用率 + 手动压缩（Trae Work 风格：对话底部展示） */}
          {ctxTokens !== null && activeId && (
            <div className="flex items-center justify-center gap-2 pt-1">
              <span className="text-[11px] text-slate-400">
                上下文使用 ≈ {ctxTokens.toLocaleString()} tokens
              </span>
              <button
                type="button"
                onClick={compactSession}
                className="text-[11px] font-medium text-brand-600 hover:underline"
                title="把早期对话压缩为摘要，释放上下文"
              >
                压缩上下文
              </button>
            </div>
          )}
          <div ref={bottomRef} />
          </div>
        </div>

        {/* 底部对话窗口（Trae Work 风格：居中输入框 + 圆形发送按钮） */}
        <div className="border-t border-slate-200 bg-white px-4 pb-4 pt-3">
          <div className="max-w-3xl mx-auto">
          <div
            className={cn(
              'rounded-2xl border bg-slate-50/80 shadow-sm focus-within:border-brand-400 focus-within:bg-white focus-within:shadow-md focus-within:ring-2 focus-within:ring-brand-500/10 transition-all relative',
              dragActive
                ? 'border-brand-400 ring-2 ring-brand-500/20 bg-brand-50/40'
                : 'border-slate-200',
            )}
            onDragOver={(e) => {
              e.preventDefault()
              setDragActive(true)
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragActive(false)
              handleFiles(e.dataTransfer.files)
            }}
          >
            {/* 斜杠命令菜单（输入 / 时弹出） */}
            {slashOpen && (
              <SlashCommandMenu
                onPick={(cmd) => {
                  setInput(cmd.template)
                  setSlashOpen(false)
                }}
                onClose={() => setSlashOpen(false)}
              />
            )}
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                // 输入 / 且当前在行首 → 打开斜杠命令菜单
                if (e.key === '/' && !e.shiftKey) {
                  const el = e.currentTarget
                  if (el.selectionStart === 0 && el.selectionEnd === 0) setSlashOpen(true)
                }
                if (e.key === 'Enter' && !e.shiftKey) {
                  // 中文输入法组词确认（composition 期间回车）不发送，仅提交候选字
                  if (e.nativeEvent.isComposing || e.keyCode === 229) return
                  e.preventDefault()
                  if (slashOpen) {
                    setSlashOpen(false)
                    return
                  }
                  send()
                }
                if (e.key === 'Escape') setSlashOpen(false)
              }}
              onPaste={(e) => {
                // P8 Phase 5：剪贴板里有文件（如截图）时直接走附件入口，纯文本粘贴不受影响
                const files = e.clipboardData?.files
                if (files && files.length > 0) {
                  e.preventDefault()
                  handleFiles(files)
                }
              }}
              rows={3}
              placeholder="输入消息，回车发送… 试试：「帮我生成一张图片」；/plan 创作规划；/goal 设定目标；/ 更多命令"
              className="w-full bg-transparent px-3 py-3 resize-none text-[14px] outline-none rounded-t-2xl focus:bg-white transition-colors"
            />
            {/* 待发送的 @ 上下文引用标签 */}
            {pendingRefs.length > 0 && (
              <div className="flex flex-wrap gap-1.5 px-3 pb-1">
                {pendingRefs.map((r, i) => (
                  <span
                    key={`${r.type}-${r.id}`}
                    className="inline-flex items-center gap-1 text-[11px] font-medium rounded-full bg-brand-50 border border-brand-200 text-brand-700 px-2 py-0.5"
                  >
                    <Icon name={r.type === 'project' ? 'folder' : r.type === 'document' ? 'book' : r.type === 'session' ? 'clapperboard' : 'image'} size={10} />
                    {r.label}
                    <button
                      type="button"
                      onClick={() => removeContextRef(i)}
                      className="hover:text-rose-500"
                      title="移除引用"
                    >
                      <Icon name="x" size={10} />
                    </button>
                  </span>
                ))}
              </div>
            )}
            {/* 待发送图片预览 */}
            {pendingImages.length > 0 && (
              <div className="flex flex-wrap gap-2 px-3 pb-1">
                {pendingImages.map((img, i) => (
                  <div
                    key={i}
                    className="relative w-14 h-14 rounded-lg overflow-hidden border border-slate-200 bg-white"
                  >
                    <img src={img} className="w-full h-full object-cover" alt="待发送图片" />
                    <button
                      type="button"
                      onClick={() => setPendingImages((prev) => prev.filter((_, j) => j !== i))}
                      className="absolute top-0 right-0 p-0.5 bg-black/55 text-white rounded-bl-md hover:bg-black/70"
                      title="移除"
                    >
                      <Icon name="x" size={10} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {/* P8 Phase 5 待发送附件预览（音频转写/视频抽帧/文档文本） */}
            {pendingAttachments.length > 0 && (
              <div className="flex flex-wrap gap-2 px-3 pb-1">
                {pendingAttachments.map((att, i) => (
                  <AttachmentPreview key={i} att={att} onRemove={() => removeAttachment(i)} />
                ))}
              </div>
            )}
            {/* 输入框底部栏：插件 + 模型 / 右侧圆形发送按钮（Trae Work 风格） */}
            <div className="flex items-center gap-2 px-3 pb-2.5">
              <PluginSelector
                plugins={plugins}
                value={activePlugins}
                onChange={setActivePlugins}
                onManage={() => setPluginOpen(true)}
                disabled={sending}
              />
              <ModelSelector models={textModels} value={modelId} onChange={setModelId} disabled={sending} />
              {/* 上传图片 / 附件 / 引用上下文 / 提示词优化 / 语音 */}
              <div className="flex items-center gap-0.5">
                <button
                  type="button"
                  title="上传文件（图片/音频/视频/PDF/Word/文本）"
                  onClick={() => fileRef.current?.click()}
                  disabled={sending || uploading}
                  className={cn(
                    'p-1.5 rounded-lg disabled:opacity-40 transition-colors',
                    uploading
                      ? 'text-brand-500'
                      : 'text-slate-500 hover:text-brand-600 hover:bg-slate-100',
                  )}
                >
                  {uploading ? (
                    <Icon name="loader-2" size={14} className="animate-spin" />
                  ) : (
                    <Icon name="paperclip" size={14} />
                  )}
                </button>
                <button
                  type="button"
                  title="引用上下文（@ 项目/文档/会话/资产）"
                  onClick={() => setAtPickerOpen(true)}
                  disabled={sending}
                  className="p-1.5 rounded-lg text-slate-500 hover:text-brand-600 hover:bg-slate-100 disabled:opacity-40"
                >
                  <Icon name="at-sign" size={14} />
                </button>
                <button
                  type="button"
                  title="优化输入内容"
                  onClick={() => optimizeInput()}
                  disabled={sending || !input.trim()}
                  className="p-1.5 rounded-lg text-slate-500 hover:text-brand-600 hover:bg-slate-100 disabled:opacity-40"
                >
                  <Icon name="wand" size={14} />
                </button>
                <button
                  type="button"
                  title={listening ? '停止录音' : '语音输入'}
                  onClick={toggleVoice}
                  disabled={sending}
                  className={cn(
                    'p-1.5 rounded-lg disabled:opacity-40 transition-colors',
                    listening
                      ? 'text-white bg-rose-500 hover:bg-rose-600 animate-pulse'
                      : 'text-slate-500 hover:text-brand-600 hover:bg-slate-100',
                  )}
                >
                  <Icon name="mic" size={14} />
                </button>
                <span className="hidden sm:inline text-[11px] text-slate-400 whitespace-nowrap">
                  Enter 发送 · Shift+Enter 换行 · 输入 / 查看命令
                </span>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept="image/*,audio/*,video/*,.pdf,.docx,.txt,.md,.json"
                multiple
                hidden
                onChange={(e) => {
                  handleFiles(e.target.files)
                  e.target.value = ''
                }}
              />
              <div className="flex-1" />
              {/* 圆形发送/停止按钮（Trae Work 风格） */}
              {sending ? (
                <button
                  type="button"
                  onClick={stop}
                  className="w-8 h-8 rounded-lg bg-slate-800 text-white flex items-center justify-center hover:bg-slate-900 transition-colors shrink-0"
                  title="停止生成"
                >
                  <Icon name="stop" size={14} />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={send}
                  disabled={!input.trim() && pendingImages.length === 0 && pendingAttachments.length === 0}
                  className="w-8 h-8 rounded-lg bg-brand-600 text-white flex items-center justify-center hover:bg-brand-700 disabled:bg-slate-200 disabled:text-slate-400 transition-colors shrink-0"
                  title="发送"
                >
                  <Icon name="arrow-up" size={16} />
                </button>
              )}
            </div>
          </div>
          </div>
        </div>
      </div>

      {/* T6 创作工作台面板：项目 / 小说写作 / 会话速览 */}
      {workbenchOpen && (
        <aside className="w-72 shrink-0 border-l border-slate-200 flex flex-col bg-white/60 overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-slate-100">
            <span className="text-xs font-medium text-slate-600 flex items-center gap-1.5">
              <Icon name="grid" size={13} className="text-brand-500" />
              创作工作台
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setScheduleOpen(true)}
                className="p-1 rounded text-slate-400 hover:text-brand-600 hover:bg-slate-100"
                title="定时自动化任务"
              >
                <Icon name="clock" size={13} />
              </button>
              <button
                type="button"
                onClick={() => setWorkbenchOpen(false)}
                className="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100"
                title="收起"
              >
                <Icon name="x" size={12} />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-4">
            {/* 进行中对话 */}
            <section>
              <h4 className="text-[11px] font-medium text-slate-400 mb-1.5 flex items-center gap-1">
                <Icon name="clapperboard" size={11} /> 当前会话
              </h4>
              <div className="text-xs text-slate-600 bg-white rounded-lg border border-slate-100 px-2.5 py-2">
                {activeId ? '消息 ' + messages.length + ' 条' : '未开始对话'}
              </div>
            </section>
            {/* 项目速览 */}
            <section>
              <h4 className="text-[11px] font-medium text-slate-400 mb-1.5 flex items-center gap-1">
                <Icon name="folder" size={11} /> 项目（{projects.length}）
              </h4>
              <div className="space-y-1.5">
                {projects.slice(0, 6).map((p) => (
                  <div key={p.id} className="bg-white rounded-lg border border-slate-100 px-2.5 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className="flex-1 truncate text-xs font-medium text-slate-700">{p.title}</span>
                      <span
                        className={cn(
                          'text-[10px] font-medium rounded-full px-1.5 py-0.5',
                          p.status === 'done'
                            ? 'bg-emerald-50 text-emerald-600'
                            : p.status === 'generating'
                              ? 'bg-amber-50 text-amber-600'
                              : p.status === 'failed'
                                ? 'bg-rose-50 text-rose-600'
                                : 'bg-slate-100 text-slate-500',
                        )}
                      >
                        {p.status}
                      </span>
                    </div>
                    {p.rules && (
                      <p className="mt-1 text-[11px] text-brand-600 truncate" title={p.rules}>
                        <Icon name="info" size={10} className="inline -mt-0.5 mr-0.5" />
                        创作规则已设置
                      </p>
                    )}
                  </div>
                ))}
                {projects.length === 0 && (
                  <p className="text-xs text-slate-400 text-center py-2">暂无项目</p>
                )}
              </div>
            </section>
            {/* 剧本库进度 */}
            <section>
              <h4 className="text-[11px] font-medium text-slate-400 mb-1.5 flex items-center gap-1">
                <Icon name="book" size={11} /> 剧本库（{novels.length}）
              </h4>
              <div className="space-y-1.5">
                {novels.slice(0, 6).map((n) => (
                  <div key={n.id} className="bg-white rounded-lg border border-slate-100 px-2.5 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className="flex-1 truncate text-xs font-medium text-slate-700">{n.title}</span>
                      <span className="text-[10px] text-slate-400 shrink-0">{n.chapters_count} 章</span>
                    </div>
                    <p className="mt-0.5 text-[11px] text-slate-400 truncate">
                      {n.word_count.toLocaleString()} 字
                      <span className="mx-1">·</span>
                      {n.analysis_status}
                    </p>
                  </div>
                ))}
                {novels.length === 0 && (
                  <p className="text-xs text-slate-400 text-center py-2">暂无剧本</p>
                )}
              </div>
            </section>
          </div>
        </aside>
      )}

      {/* P9 会话级工具权限弹层 */}
      <ToolWhitelistDialog
        open={toolPermOpen}
        current={currentWhitelist}
        onClose={() => setToolPermOpen(false)}
        onSave={saveToolWhitelist}
      />
      {/* @ 引用上下文弹层（项目/文档/会话/资产） */}
      <ContextPickerDialog open={atPickerOpen} onClose={() => setAtPickerOpen(false)} onSelect={addContextRef} />
      {/* 生成结果保存到剧本弹层 */}
      {saveTarget && <SaveToNovelDialog imageUrl={saveTarget} onClose={() => setSaveTarget(null)} />}
      {/* 记忆管理弹层 */}
      <MemoryDialog open={memoryOpen} onClose={() => setMemoryOpen(false)} />
      {/* Skill 管理弹层 */}
      <SkillDialog open={skillOpen} onClose={() => setSkillOpen(false)} />
      {/* MCP 服务器管理弹层 */}
      <McpServerDialog open={mcpOpen} onClose={() => setMcpOpen(false)} />
      {/* 规则管理弹层（对齐 TraeWork Rules） */}
      <RuleDialog open={ruleOpen} onClose={() => setRuleOpen(false)} />
      {/* P8 定时自动化任务弹层 */}
      <ScheduleDialog open={scheduleOpen} onClose={() => setScheduleOpen(false)} />
      {/* 生成插件管理弹层（对齐 TraeWork：选择插件 → 描述需求 → 执行） */}
      <PluginDialog open={pluginOpen} onClose={() => setPluginOpen(false)} />
    </div>
  )
}

// ─── 模型选择器（Trae Work 风格：对话窗口内按钮 + 弹出列表）────────



function toLocalMsg(m: AgentMessage): LocalMsg {
  return {
    key: m.id,
    role: m.role,
    content: m.content ?? '',
    dbId: m.id,
    images: m.images ?? undefined,
    attachments: m.attachments ?? undefined,
    toolName: m.tool_name ?? undefined,
    toolStatus: m.tool_status ?? undefined,
    mediaUrls: m.media_urls ?? undefined,
    projectId: m.project_id ?? undefined,
    toolParams: m.tool_params ?? undefined,
    // 历史回放：思考过程已入库，恢复为「已思考」折叠态（thinkingDone=true）；
    // 未完成占位消息（completed=false，切走中断残留）保持思考展开 + 生成中态
    thinking: m.thinking ?? undefined,
    thinkingDone: !!m.thinking && m.completed !== false,
    completed: m.completed !== false,
  }
}

/** DB 工具行 → 助手消息内嵌工具块（时序块模型，1:1 对齐 dsh web 单消息渲染） */
function toToolPart(m: AgentMessage): AssistantPart {
  return {
    kind: 'tool',
    key: `pt-${m.id}`,
    dbId: m.id,
    toolName: m.tool_name ?? undefined,
    toolStatus: m.tool_status ?? undefined,
    content: m.content ?? '',
    mediaUrls: m.media_urls ?? undefined,
    projectId: m.project_id ?? undefined,
    toolParams: m.tool_params ?? undefined,
  }
}

/**
 * 历史回放分组：把 DB 中「独立存放」的 tool 行归并进最近的前一条 assistant 消息，
 * 作为其内部时序块渲染（正文块在前，工具卡依次在后）——与实时流式交错渲染一致，
 * 避免工具卡散落成独立行、正文与工具同屏堆叠的混乱观感。
 * 无归属 assistant 的孤立工具行（异常数据兜底）仍以独立行展示。
 */
function groupHistory(msgs: AgentMessage[]): LocalMsg[] {
  const out: LocalMsg[] = []
  let cur: LocalMsg | null = null
  for (const m of msgs) {
    if (m.role === 'tool') {
      if (cur) {
        const base = cur
        const parts = base.parts ?? []
        // 结果内容去重：同工具同结果的历史行只保留一张（防回放时重复卡堆积）
        const dup = parts.some(
          (p) =>
            p.kind === 'tool' &&
            p.toolName === m.tool_name &&
            (p.content ?? '') === (m.content ?? '') &&
            m.content,
        )
        if (!dup) {
          out[out.length - 1] = { ...base, parts: [...parts, toToolPart(m)] }
        }
      } else {
        out.push(toLocalMsg(m))
      }
      continue
    }
    const lm = toLocalMsg(m)
    out.push(lm)
    if (m.role === 'assistant') {
      // 正文统一作为第一个文本块（工具块随后追加），渲染顺序 = 正文 + 工具卡
      lm.parts = [{ kind: 'text', key: `pt-${m.id}-0`, text: m.content ?? '' }]
      cur = lm
    } else {
      cur = null // user（或其他）消息：重置工具归属
    }
  }
  return out
}

// ─── P8 Phase 5 多模态附件预览（输入框待发送 / 用户消息展示共用）────────





