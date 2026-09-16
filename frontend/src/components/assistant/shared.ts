/**
 * 创作助手共享符号（Assistant.tsx 拆分产物 · 2026-08-17）
 *
 * 原单体中全部模块级类型 / 常量 / 纯函数收口于此，各子组件按需精确 import。
 * 逻辑未改动（剥壳不换芯）。
 */
import type {
  AgentAttachment, AgentGoal, AgentPlan, AspectRatio, NovelOutline,
} from '../../api/types'

export interface LocalMsg {
  key: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  dbId?: string
  images?: string[]
  /** P8 Phase 5 多模态附件（用户消息展示：图片/音频转写/视频帧/文档摘要） */
  attachments?: AgentAttachment[]
  toolName?: string
  toolStatus?: string
  toolStep?: string
  mediaUrls?: string[]
  projectId?: string
  toolParams?: Record<string, unknown>
  streaming?: boolean
  error?: string
  /** Plan 工作流：规划文档消息（渲染为规划卡片） */
  plan?: AgentPlan
  /** Goal 工作流：目标消息（渲染为目标卡片） */
  goal?: AgentGoal
  /** 流式思考过程（reasoning_content 累积，渲染为可折叠「思考过程」区） */
  thinking?: string
  /** 思考是否已结束（流结束置位，允许收起折叠区） */
  thinkingDone?: boolean
  /** 服务端是否已生成完成（历史回放占位消息=false 时显示「思考中」并轮询） */
  completed?: boolean
  /** 长篇小说工作流：大纲消息（渲染为大纲预览卡片，确认后提交写作） */
  novelOutline?: NovelOutline
  /** 长篇小说工作流：写作任务消息（轮询任务进度） */
  novelWriting?: { novelId: string; taskId: string; title: string; chapters: number }
  /**
   * 助手消息内的时序块列表（1:1 对齐 dsh web：文本与工具卡按「发生顺序」交错渲染在
   * 同一条助手气泡内——先思考 → 工具依次执行 → 结果整理汇报，而不是并行堆叠）。
   * kind=text 存正文片段；kind=tool 存工具卡快照（字段与独立 tool 消息一致）。
   */
  parts?: AssistantPart[]
}

/** 助手消息内部的时序块（文本 / 工具调用） */
export interface AssistantPart {
  kind: 'text' | 'tool'
  key: string
  /** 工具块对应的 DB 消息 id（回放时来自 tool 行；确认类工具卡需要） */
  dbId?: string
  /** kind === 'text' 时的正文片段 */
  text?: string
  // kind === 'tool' 时使用以下字段（与独立 tool 消息对齐）
  toolName?: string
  toolStatus?: string
  toolStep?: string
  content?: string
  mediaUrls?: string[]
  projectId?: string
  toolParams?: Record<string, unknown>
}

/** create_project 草案载荷（后端 pending_project_draft：会话剧本内容结构化整理结果） */
export interface ProjectDraftPayload {
  draft: {
    title?: string
    synopsis?: string
    script?: string
    assets?: { type: string; name: string; description?: string }[]
    episodes?: { title?: string; synopsis?: string; segments?: { shot_type?: string; camera?: string; description?: string }[] }[]
  }
  summary: string
  session_id: string
  title: string
  default: { aspect_ratio: AspectRatio; style_id: string | null; art_style_prompt: string | null }
}

/** project_delete 待删载荷（后端 pending_project_delete：项目名+id+内容统计） */
export interface ProjectDeletePayload {
  project_id: string
  title: string
  detail: string
}

/**
 * 创建项目确认回调载荷：画面尺寸 / 美术风格 / 项目级视频参数。
 * resolution：视频分辨率（480p/720p）；video_params：项目级视频生成参数
 * （fps/res/video_size/steps/cfg/seed/turbo，批量生成按项目级出片，分镜级优先）。
 */
export interface ConfirmProjectOpts {
  aspect_ratio: AspectRatio
  style_id: string | null
  art_style_prompt: string | null
  resolution?: string | null
  video_params?: Record<string, unknown> | null
}

export const ASPECT_RATIOS: { value: AspectRatio; label: string; box: string }[] = [
  { value: '9:16', label: '9:16 竖屏', box: 'w-3 h-5' },
  { value: '16:9', label: '16:9 横屏', box: 'w-5 h-3' },
  { value: '1:1', label: '1:1 方形', box: 'w-4 h-4' },
  { value: '4:3', label: '4:3', box: 'w-5 h-4' },
  { value: '3:4', label: '3:4', box: 'w-4 h-5' },
]

export const TOOL_LABELS: Record<string, string> = {
  generate_image: '生成图片',
  generate_video: '生成视频',
  create_project: '创建项目',
  web_search: '联网搜索',
  'ai.web_search': '联网搜索',
  github_search: 'GitHub 仓库搜索',
  web_fetch: '读取网页',
  write_novel: '长篇小说写作',
  write_script: '剧本写作',
  install_skill: '安装技能',
  skill_list: '技能清单',
  skill_view: '加载技能',
  terminal_execute: '终端命令',
  file_read: '读取文件',
  file_write: '写入文件',
  browser_navigate: '浏览器打开网页',
  browser_snapshot: '浏览器查看页面',
  browser_click: '浏览器点击',
  browser_type: '浏览器输入',
  browser_screenshot: '浏览器截图',
  code_search: '代码搜索',
  code_list: '文件列表',
  code_read: '读取代码',
  code_edit: '编辑代码',
  code_write: '写入代码',
  code_execute: '沙箱执行代码',
  code_diagnose: '代码诊断',
  code_rollback: '检查点回滚',
  git_status: 'Git 状态',
  git_diff: 'Git Diff',
  git_commit: 'Git 提交',
  git_log: 'Git 提交历史',
  git_branch: 'Git 分支',
  git_push: 'Git 推送',
  tts_speak: '语音朗读',
  project_list: '项目列表',
  project_delete: '删除项目',
}

export interface SearchItem {
  title?: string
  url?: string
  snippet?: string
}

/** 解析工具结果文本为搜索结果来源列表：JSON（{items:[{title,url,snippet}]} 或裸数组）→ markdown 链接行。 */
export function parseSearchItems(content: string): SearchItem[] {
  const items: SearchItem[] = []
  if (!content) return items
  try {
    const parsed = JSON.parse(content)
    const arr = Array.isArray(parsed) ? parsed : parsed?.items
    if (Array.isArray(arr)) {
      return arr
        .filter((x) => x && (x.title || x.url))
        .map((x) => ({
          title: String(x.title ?? ''),
          url: String(x.url ?? ''),
          snippet: String(x.snippet ?? x.desc ?? ''),
        }))
    }
  } catch {
    /* 非 JSON，退回 markdown 解析 */
  }
  const linkRe = /(?:^|\n)\s*[-*]\s*\[([^\]]+)\]\(([^)\s]+)\)\s*([^\n]*)/g
  let m: RegExpExecArray | null
  while ((m = linkRe.exec(content)) !== null) {
    if (items.length >= 12) break
    items.push({ title: m[1], url: m[2], snippet: (m[3] ?? '').trim() })
  }
  return items
}



export const WELCOME_MSG = `你好！我是「创作助手」。我可以：
• 和你讨论创意、打磨剧本、分析角色与镜头
• 生成图片、生成视频（说句话即可）
• 帮你创建短剧项目
• 联网搜索最新信息

试试对我说：「帮我生成一张夕阳下的古风少女立绘」或「帮我创建一个武侠短剧项目」。`

export const SUGGESTIONS: { icon: string; title: string; desc: string; prompt: string }[] = [
  {
    icon: 'sparkles',
    title: '生成角色立绘',
    desc: '古风少女 · 电影级质感',
    prompt: '帮我生成一张古风少女立绘：月下竹林、长发随风飘扬，电影级光影质感，全身像，适合作为短剧女主角',
  },
  {
    icon: 'clapperboard',
    title: '创建短剧项目',
    desc: '一句话开启你的短剧',
    prompt: '帮我创建一个悬疑短剧项目：雨夜古宅、失忆女主与一封十年后才寄到的信，先搭好项目框架',
  },
  {
    icon: 'video',
    title: '生成视频片段',
    desc: '文生视频 · 电影感镜头',
    prompt: '帮我生成一段 8 秒短视频：雨夜古巷中红衣女子撑伞回眸，镜头缓缓推进，电影级光影',
  },
  {
    icon: 'brain',
    title: '打磨剧本',
    desc: '优化节奏与悬念设计',
    prompt: '帮我优化剧本开篇三分钟的节奏：主角登场太晚，观众容易流失，请重新设计悬念钩子',
  },
]

/** Trae Work 风格欢迎页：大图标 + 欢迎语 + 建议提示词快捷入口 */


export const ATTACH_KIND_LABELS: Record<string, string> = {
  image: '图片',
  audio: '音频',
  video: '视频',
  document: '文档',
}
export const ATTACH_KIND_ICONS: Record<string, string> = {
  image: 'image',
  audio: 'mic',
  video: 'video',
  document: 'file-text',
}



export const TOOL_GROUPS: { label: string; keys: string[] }[] = [
  // 2026-08-23 能力边界：智能体仅保留「写剧本」单一创作能力（剧本+H3分镜+海报+项目）
  { label: '剧本创作', keys: ['write_script'] },
]


export const ASSET_TYPE_LABELS: Record<string, string> = { character: '角色', scene: '场景', prop: '道具' }

export type ContextTab = 'project' | 'document' | 'session' | 'asset'

export const CTX_TABS: Array<{ key: ContextTab; label: string; icon: string }> = [
  { key: 'project', label: '项目', icon: 'folder' },
  { key: 'document', label: '剧本文档', icon: 'book' },
  { key: 'session', label: '历史会话', icon: 'clapperboard' },
  { key: 'asset', label: '资产', icon: 'image' },
]



export const SLASH_COMMANDS = [
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



export const BUILTIN_ROLES = [
  { role_key: 'screenwriter', name: '编剧', desc: '故事结构、人物弧光、对白打磨' },
  { role_key: 'director', name: '导演', desc: '镜头语言、分镜设计、光影氛围' },
  { role_key: 'artist', name: '美术指导', desc: '视觉风格、角色造型、场景氛围' },
]

/** 可选工具白名单（与后端 _TOOLS 名称一致） */
export const ALL_TOOL_KEYS = Object.keys(TOOL_LABELS)


export const SKILL_TYPE_LABELS: Record<string, string> = {
  builtin_tool: '内置工具映射',
  prompt: '可调用技能',
  knowledge: '知识注入',
}



export const PLUGIN_ICONS: Record<string, string> = {
  t2i: 'image', i2i: 'wand', t2v: 'video', i2v: 'film', multi_ref: 'layers',
}



export const PLUGIN_MODE_LABELS: Record<string, string> = {
  t2i: '文生图', i2i: '图生图', t2v: '文生视频', i2v: '图生视频', multi_ref: '多图参考生视频',
}



