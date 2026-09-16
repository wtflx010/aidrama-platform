"""创作助手（Agent）数据模型：会话 / 消息 / Skill 库。

2026-08-11 新建（P1）：类 ChatGPT 的独立聊天功能。
- agent_session：会话（对话持久化）
- agent_message：消息（user/assistant/tool 三种角色，工具消息携带参数/结果/媒体 URL）
- agent_skill：可配置 Skill 库（首期内置生图/生视频/建项目/联网搜索，后续可自定义扩展）
"""
import uuid

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class AgentSession(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "agent_session"

    title: Mapped[str] = mapped_column(String(200), default="新对话", nullable=False)
    # 长对话历史摘要缓存（上下文裁剪时生成，注入 system prompt，避免超长上下文）
    summary: Mapped[str | None] = mapped_column(Text)
    # P9 会话级工具白名单：NULL=全部工具可用；非空列表=仅该列表内工具可用（与角色白名单取交集）
    tool_whitelist: Mapped[list | None] = mapped_column(JSON)


class AgentMessage(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "agent_message"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user / assistant / tool
    content: Mapped[str | None] = mapped_column(Text)  # 文本内容（assistant 最终回复 / 工具结果摘要）
    # 推理模型思考过程（reasoning_content，仅前端展示用，不注入对话上下文；assistant 消息携带）
    thinking: Mapped[str | None] = mapped_column(Text)
    # 流式增量落库标记：False=生成中（客户端切走/断连后 DB 中保留的进行中消息，
    # 前端据此展示「思考中」并轮询直至 completed=True；不回放进对话上下文）
    completed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 用户消息携带的图片（data URI 或媒体 URL；供视觉理解与按图生图参考）
    images: Mapped[list | None] = mapped_column(JSON)
    # P9 多模态附件持久化（POST /agent/attachments 处理结果 dict 列表：image/audio/video/document）
    # 历史回放时重新注入视觉/转写/文本，支持跨会话回溯续聊
    attachments: Mapped[list | None] = mapped_column(JSON)
    # 工具消息字段（role=tool 时使用）
    tool_name: Mapped[str | None] = mapped_column(String(100))
    tool_params: Mapped[dict | None] = mapped_column(JSON)  # 工具入参
    tool_status: Mapped[str | None] = mapped_column(String(20))  # running / succeeded / failed
    # 工具产出的媒体结果（生图 → [image_url]；生视频 → [video_url]）
    media_urls: Mapped[list | None] = mapped_column(JSON)
    # 创建项目工具产出：关联项目 id（前端可跳转）
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL")
    )


class AgentSubagentMessage(UUIDPkMixin, TimestampMixin, Base):
    """子智能体独立会话消息（P9 ③ 子智能体独立会话，2026-08-11）。

    每个 (主会话 session_id, 子智能体 role_key) 保留自己的「任务→产出」历史，
    下次调用该子智能体时注入其最近几轮历史（独立上下文），跨调用保持设定/风格连续。
    仅子智能体内部使用，不进入主会话消息流；主会话删除时级联清理。
    """
    __tablename__ = "agent_subagent_message"
    __table_args__ = (
        # 独立会话历史查询：按 (主会话, 子智能体) 取最近 N 轮
        Index("ix_agent_subagent_session_role", "session_id", "role_key"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # screenwriter/director/artist…
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user（任务）/ assistant（产出）
    content: Mapped[str] = mapped_column(Text, nullable=False)


class AgentCreativeState(UUIDPkMixin, TimestampMixin, Base):
    """创作状态卡（P1 状态层，2026-08-11）：记录会话中各创作角色「当前生效版本」。

    每个 (session_id, role_key) 一行：子智能体每次产出新内容时覆盖更新（version 自增）。
    创建项目等需要"当前剧本/设定"的工具直接读取本表，不再从聊天历史中推断哪个版本最新，
    从根上避免「新旧剧本并存时拿错版本」的问题。会话删除时级联清理。
    role_key: screenwriter（编剧）/ director（导演）/ artist（美术）
    """
    __tablename__ = "agent_creative_state"
    __table_args__ = (
        Index(
            "ix_agent_creative_state_session_role",
            "session_id", "role_key", unique=True,
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_key: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 当前版本序号（该角色自增）
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 当前版本内容快照


class AgentSkill(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "agent_skill"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # 注入模型的 Skill 说明（模型据此决定何时调用/如何使用）
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # builtin_tool=内置工具（生图/生视频/建项目/搜索，执行逻辑在后端硬编码，handler 填内置工具名）；
    # prompt=可调用提示词技能（模型调用 skill_<name> 时按 prompt 执行子任务生成结果）；
    # knowledge=纯知识注入（仅增强上下文，无执行逻辑）
    tool_type: Mapped[str] = mapped_column(String(30), default="knowledge", nullable=False)
    # tool_type=builtin_tool 时映射的内置工具名（如 web_search/generate_image），其余类型为空
    handler: Mapped[str | None] = mapped_column(String(100))
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AgentRule(UUIDPkMixin, TimestampMixin, Base):
    """创作助手规则（对齐 TraeWork Rules，2026-08-11）。

    全局/项目级规则持久化，自动注入 system prompt 约束智能体行为
    （如语言偏好、风格要求、禁止事项、默认参数等）。
    """
    __tablename__ = "agent_rule"

    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 规则标题（描述性，如「默认画幅 9:16」）
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 规则正文（注入 system prompt 的约束文本）
    scope: Mapped[str] = mapped_column(String(20), default="global", nullable=False)  # global / project
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AgentMcpServer(UUIDPkMixin, TimestampMixin, Base):
    """MCP 服务器配置（创作助手可调用的外部工具源，2026-08-11）。

    支持 stdio 本地进程：command + args 启动 MCP 服务器（如 npx @modelcontextprotocol/server-github），
    启动后通过 MCP 协议发现其工具并动态注册为对话工具（mcp__<server>__<tool>）。
    """
    __tablename__ = "agent_mcp_server"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    command: Mapped[str] = mapped_column(String(500), nullable=False)  # 启动命令（可执行文件）
    args: Mapped[list | None] = mapped_column(JSON)  # 命令参数列表
    env: Mapped[dict | None] = mapped_column(JSON)  # 附加环境变量（如 API Key）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AgentMemory(UUIDPkMixin, TimestampMixin, Base):
    """创作助手长期记忆：对话中「记住/忘记」管理，注入 system prompt 跨会话生效。"""
    __tablename__ = "agent_memory"

    scope: Mapped[str] = mapped_column(String(20), default="global", nullable=False)  # global / project
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 项目级记忆：scope=project 时关联项目（跨会话注入，仅当 @ 引用该项目时生效）
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE")
    )


class AgentPlan(UUIDPkMixin, TimestampMixin, Base):
    """创作规划（Plan 工作流）：把用户需求拆解为分步规划文档，确认后分步执行。

    status: draft（待确认）/ confirmed（已确认，可执行）/ done（全部完成）
    steps: [{title, description, status}]  status=pending/done
    """
    __tablename__ = "agent_plan"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 完整规划文档（Markdown）
    steps: Mapped[list | None] = mapped_column(JSON)  # [{title, description, status}]
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL")
    )


class AgentGoal(UUIDPkMixin, TimestampMixin, Base):
    """创作目标（Goal 工作流）：设定目标后 AI 多轮自动推进，每轮自评是否达成。

    status: active（进行中）/ paused（暂停）/ done（已达成）
    """
    __tablename__ = "agent_goal"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)  # 目标描述
    criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 完成标准
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    progress_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 当前进度摘要
    step_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 已推进轮数
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL")
    )


class AgentRoleConfig(UUIDPkMixin, TimestampMixin, Base):
    """创作角色配置（T2 可配置创作角色/子智能体，2026-08-11）。

    kind: chat（对话角色，注入人设+工具白名单）/ subagent（子智能体，独立任务执行者）
    persona: 角色人设（system prompt）
    tools: chat 角色允许调用的工具白名单（None=全部）；subagent 忽略
    """
    __tablename__ = "agent_role_config"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    role_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="chat", nullable=False)  # chat / subagent
    persona: Mapped[str] = mapped_column(Text, nullable=False)
    tools: Mapped[list | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AgentPlugin(UUIDPkMixin, TimestampMixin, Base):
    """生成插件（对齐 TraeWork 插件：对话前选择插件 → 描述需求 → 执行，2026-08-11）。

    每个插件绑定一个内置工具（tool）与执行模式（mode）：
    - tool: generate_image（生图）/ generate_video（生视频）/ web_search 等内置工具名
    - mode: t2i（文生图）/ i2i（图生图，需参考图）/ t2v（文生视频）/
            i2v（图生视频，首帧/单图参考）/ multi_ref（多图参考生视频）
    prompt: 选中插件后注入 system prompt 的说明与约束（模型据此正确调用工具并传参）
    """
    __tablename__ = "agent_plugin"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)  # 唯一标识（如 t2i / i2v）
    label: Mapped[str] = mapped_column(String(100), nullable=False)  # 显示名（如 文生图）
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 注入 system prompt 的执行约束
    tool: Mapped[str] = mapped_column(String(100), nullable=False)  # 绑定的内置工具名
    mode: Mapped[str] = mapped_column(String(30), default="t2i", nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AgentSchedule(UUIDPkMixin, TimestampMixin, Base):
    """定时自动化任务（P8，对齐 Hermes/OpenClaw cron 调度，2026-08-11）。

    - cron_expr：5 字段 cron 表达式（分 时 日 月 周），由 beat tick 每 30s 扫描判断到期
    - action_type=prompt：定时让 LLM 生成内容并写入指定会话（如每日进度报告）
    - action_type=system：执行系统动作（如 retry_failed_tasks 重试失败任务）
    - session_id：prompt 类产出的写入目标会话；为空则每次运行自动新建
    - 生成类（生图/生视频）不在定时动作范围：遵守「禁止系统自动触发生成类任务」约束
    """
    __tablename__ = "agent_schedule"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cron_expr: Mapped[str] = mapped_column(String(50), nullable=False)  # 5 字段 cron
    action_type: Mapped[str] = mapped_column(String(20), default="prompt", nullable=False)  # prompt / system
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)  # prompt 类的生成指令
    system_action: Mapped[str] = mapped_column(String(50), default="", nullable=False)  # retry_failed_tasks 等
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_session.id", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
