"""创作助手（Agent）相关 schema：会话 / 消息 / Skill / 对话请求。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ─── 会话 ─────────────────────────────────────────────

class AgentSessionCreate(BaseModel):
    title: str | None = None  # 为空则按首条消息自动命名


class AgentSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    # P9 会话级工具白名单（NULL=全部工具可用）
    tool_whitelist: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class AgentSearchHit(BaseModel):
    """跨会话搜索命中项（P8 Phase 3）：消息摘要 + 所属会话，供前端跳转定位。"""
    session_id: str
    session_title: str
    message_id: str
    role: str  # user / assistant
    snippet: str
    created_at: str | None


# ─── 消息 ─────────────────────────────────────────────

class AgentMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    role: str  # user / assistant / tool
    content: str | None
    # 推理模型思考过程（reasoning_content，assistant 消息携带，前端折叠展示）
    thinking: str | None = None
    # 流式增量落库标记：False=生成中（前端显示「思考中」并轮询直至 True）
    completed: bool = True
    images: list[str] | None = None  # 用户消息携带图片（data URI / 媒体 URL）
    # P9 多模态附件持久化（kind/name/url/transcript/frames/text），前端展示 + 历史回放注入
    attachments: list[dict] | None = None
    tool_name: str | None
    tool_params: dict | None
    tool_status: str | None  # running / succeeded / failed
    media_urls: list[str] | None
    project_id: UUID | None
    created_at: datetime


# ─── Skill 库 ─────────────────────────────────────────

class AgentSkillCreate(BaseModel):
    name: str
    description: str
    prompt: str
    tool_type: str = "knowledge"  # builtin_tool / prompt（可调用）/ knowledge（纯注入）
    handler: str | None = None  # builtin_tool 时映射的内置工具名
    enabled: bool = True
    sort: int = 0


class AgentSkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    tool_type: str | None = None
    handler: str | None = None
    enabled: bool | None = None
    sort: int | None = None


class AgentSkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    prompt: str
    tool_type: str
    handler: str | None = None
    is_builtin: bool
    enabled: bool
    sort: int
    created_at: datetime


# ─── 规则（Rules，对齐 TraeWork：全局/项目级规则自动注入）─────────────

class AgentRuleCreate(BaseModel):
    name: str
    content: str
    scope: str = "global"  # global / project
    project_id: UUID | None = None
    enabled: bool = True
    sort: int = 0


class AgentRuleUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    scope: str | None = None
    project_id: UUID | None = None
    enabled: bool | None = None
    sort: int | None = None


class AgentRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    content: str
    scope: str
    project_id: UUID | None = None
    enabled: bool
    sort: int
    created_at: datetime


# ─── MCP 服务器配置 ──────────────────────────────────

class AgentMcpServerCreate(BaseModel):
    name: str
    description: str = ""
    command: str
    args: list[str] = []
    env: dict[str, str] | None = None
    enabled: bool = True
    sort: int = 0


class AgentMcpServerUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None
    sort: int | None = None


class AgentMcpServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None
    enabled: bool
    sort: int
    created_at: datetime


class AgentMcpTestResult(BaseModel):
    """连接测试结果：成功返回发现的工具列表。"""
    ok: bool
    message: str = ""
    tools: list[dict] = []  # OpenAI function 定义


# ─── 对话请求 ─────────────────────────────────────────

class ContextRef(BaseModel):
    """@ 引用的上下文资源（项目 / 剧本文档 / 历史会话 / 资产）。"""
    type: Literal["project", "document", "session", "asset"]
    id: str
    label: str = ""


class AgentChatRequest(BaseModel):
    """发送一条对话消息。session_id 为空时自动新建会话。"""
    message: str | None = None  # 普通对话必填；regenerate_message_id 存在时可为空（复用原用户消息）
    session_id: UUID | None = None
    model_id: UUID | None = None  # 指定对话模型，空则用默认文本模型
    regenerate_message_id: UUID | None = None  # 指向某条 assistant 消息：删除其后所有消息并按原问题重新生成
    images: list[str] = []  # 用户上传图片（data URI 或媒体 URL）：视觉理解 + 按图生图参考
    attachments: list[dict] = []  # P8 Phase 5 多模态附件（POST /agent/attachments 处理结果）：image/audio/video/document
    context_refs: list[ContextRef] = []  # @ 引用的上下文（项目/文档/会话/资产），注入 system prompt
    agent_role: str | None = None  # 创作角色预设：general/screenwriter/director/artist
    plugins: list[str] = []  # 选中的生成插件名列表（如 ["i2v"]）；空=不启用任何插件（模型自主判断）


# ─── 生成插件（对齐 TraeWork 插件，2026-08-11）─────────

class AgentPluginCreate(BaseModel):
    name: str  # 唯一标识（小写字母/数字/下划线，如 t2i / i2v / multi_ref）
    label: str  # 显示名（如 文生图）
    description: str = ""
    prompt: str = ""  # 选中插件后注入 system prompt 的执行约束
    tool: str  # 绑定的内置工具名（generate_image / generate_video / web_search 等）
    mode: str = "t2i"  # t2i / i2i / t2v / i2v / multi_ref
    enabled: bool = True
    sort: int = 0


class AgentPluginUpdate(BaseModel):
    label: str | None = None
    description: str | None = None
    prompt: str | None = None
    tool: str | None = None
    mode: str | None = None
    enabled: bool | None = None
    sort: int | None = None


class AgentPluginOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    label: str
    description: str
    prompt: str
    tool: str
    mode: str
    is_builtin: bool
    enabled: bool
    sort: int
    created_at: datetime


# ─── 长期记忆 ─────────────────────────────────────────

class AgentMemoryCreate(BaseModel):
    content: str
    scope: str = "global"  # global / project
    project_id: UUID | None = None


class AgentMemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scope: str
    content: str
    project_id: UUID | None = None
    created_at: datetime


class AgentMemoryHit(BaseModel):
    """P9 记忆语义检索命中项：内容 + 相关度得分（0~1）。"""
    id: str
    scope: str
    content: str
    score: float


# ─── 创作规划（Plan 工作流）──────────────────────────

class AgentPlanCreate(BaseModel):
    """生成创作规划文档。"""
    session_id: UUID
    message: str  # 用户需求（如「写一部 12 集古装复仇短剧」）
    model_id: UUID | None = None

class AgentPlanStep(BaseModel):
    """规划中的单个步骤。"""
    title: str
    description: str
    status: str = "pending"  # pending / done


class AgentPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    title: str
    content: str
    steps: list[AgentPlanStep] | None = None
    status: str  # draft / confirmed / done
    project_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ─── 创作目标（Goal 工作流）──────────────────────────

class AgentGoalCreate(BaseModel):
    """设定创作目标：AI 多轮自动推进，每轮自评是否达成。"""
    session_id: UUID
    message: str  # 目标描述（如「完成 12 集短剧大纲」）
    model_id: UUID | None = None


class AgentGoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    title: str
    goal: str
    criteria: str
    status: str  # active / paused / done
    progress_summary: str
    step_count: int
    project_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ─── 长篇小说写作（/novel 工作流）──────────────────

class NovelOutlineChapter(BaseModel):
    index: int
    title: str
    brief: str


class NovelOutlineBody(BaseModel):
    """生成小说大纲（供前端预览确认，不落库）。"""
    brief: str  # 题材设定/一句话故事描述
    genre: str | None = None
    chapters: int = 10
    model_id: UUID | None = None


class NovelOutlineOut(BaseModel):
    title: str
    genre: str
    logline: str
    world: str
    chapters: list[NovelOutlineChapter]


class NovelWriteBody(BaseModel):
    """创建小说 + 提交后台逐章写作任务。outline 为空时由任务自动规划大纲。"""
    title: str
    brief: str
    genre: str | None = None
    chapters: int = 10
    outline: dict | None = None  # /novel 命令确认后回传的大纲；为空则任务内生成
    model_id: UUID | None = None


class NovelWriteOut(BaseModel):
    novel_id: UUID
    task_id: UUID
    title: str


# ─── 创作角色配置（可配置创作角色/子智能体，T2）────────

class AgentRoleCreate(BaseModel):
    """创建创作角色。kind: chat（对话角色）/ subagent（子智能体）。"""
    name: str
    role_key: str  # 唯一标识（如 my_screenwriter），模型/接口用它引用
    kind: str = "chat"  # chat / subagent
    persona: str  # 角色人设（system prompt）
    tools: list[str] | None = None  # chat 角色工具白名单；None=全部
    enabled: bool = True
    sort: int = 0


class AgentRoleUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    persona: str | None = None
    tools: list[str] | None = None
    enabled: bool | None = None
    sort: int | None = None


class AgentRoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role_key: str
    kind: str
    persona: str
    tools: list[str] | None = None
    enabled: bool
    sort: int
    created_at: datetime


# ─── 定时自动化任务（P8，对齐 Hermes/OpenClaw cron，2026-08-11）──

class AgentScheduleCreate(BaseModel):
    name: str
    cron_expr: str  # 5 字段 cron（分 时 日 月 周）
    action_type: str = "prompt"  # prompt（定时对话/报告）/ system（系统动作）
    prompt: str = ""  # action_type=prompt 时的 LLM 生成指令
    system_action: str = ""  # action_type=system 时的动作名（retry_failed_tasks）
    session_id: UUID | None = None  # prompt 类产出写入的会话；空=每次自动新建
    enabled: bool = True


class AgentScheduleUpdate(BaseModel):
    name: str | None = None
    cron_expr: str | None = None
    action_type: str | None = None
    prompt: str | None = None
    system_action: str | None = None
    session_id: UUID | None = None
    enabled: bool | None = None


class AgentScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    cron_expr: str
    action_type: str
    prompt: str
    system_action: str
    session_id: UUID | None
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    run_count: int
    last_error: str | None
    created_at: datetime
