"""创作助手（Agent）服务 —— 兼容门面（A 方案重构后）。

原 6094 行单体已拆分至 `app.services.agent` 分层包（引擎内核层 / 工具注册层 /
创作对接层 / 编排工作流层）。本文件仅 re-export 全部对外符号，保证
`agent.py` / `gen.py` / `agent_schedule_service.py` 等调用方零改动。

重构史：2026-08-17 S1~S8（见 docs/智能体重构-A方案-设计.md §10）。
"""

# 会话/模型类型（API 层直接引用）
from app.models.agent import AgentSession  # noqa: F401

# ── 引擎内核层 ──────────────────────────────
from app.services.agent.agents.goals import (  # noqa: F401
    create_goal,
    evaluate_goal,
    get_goal,
    goal_advance_message,
    list_goals,
    set_goal_status,
)

from app.services.agent.agents.novel_api import (  # noqa: F401
    generate_novel_outline,
    submit_novel_writing,
)

from app.services.agent.agents.plans import (  # noqa: F401
    confirm_plan,
    create_plan,
    delete_plan,
    get_plan,
    list_plans,
    mark_plan_step_done,
)

from app.services.agent.creative.media import (  # noqa: F401
    _ensure_local_ref,
    _poll_sync,
    _tool_generate_image,
    _tool_generate_video,
    _tool_tts_speak,
    _vision_qc,
)

from app.services.agent.creative.project import (  # noqa: F401
    _PROJECT_DRAFT_PROMPT,
    _build_project_draft,
    _tool_create_project_draft,
    confirm_project,
    project_delete_confirm,
)

from app.services.agent.creative.subagent import (  # noqa: F401
    _collect_creative_state_context,
    _collect_script_context,
    _load_subagent_history,
    _maybe_track_main_creative,
    _run_skill,
    _run_subagent,
    _run_subagent_core,
    _run_subagents_parallel,
    _save_subagent_turn,
    _upsert_creative_state,
)

from app.services.agent.creative.writing import (  # noqa: F401
    _tool_write_novel,
    _tool_write_script,
)

from app.services.agent.engine.chunks import (  # noqa: F401
    _accumulate_tool_calls,
    _extract_delta,
    _extract_reasoning,
)

from app.services.agent.engine.constants import (  # noqa: F401
    _CHAT_TOTAL_TIMEOUT,
    _CTX_REF_LIMITS,
    _MAIN_CREATIVE_NOUNS,
    _MAIN_CREATIVE_VERBS,
    _MAX_HISTORY,
    _MAX_TOOL_ROUNDS,
    _SUBAGENT_HISTORY_TURNS,
    _SUBAGENT_TOOLS,
    _SUMMARY_THRESHOLD,
    _SYSTEM_PROMPT,
    _TOOLCALL_TEXT_RE,
    _has_main_creative_intent,
)

from app.services.agent.engine.context import (  # noqa: F401
    _auto_generate_title,
    _build_context_messages,
    _build_system_prompt,
    _ctx_tokens,
    _digest_attachments,
    _resolve_context_refs,
    _summarize_history,
    _to_chat_messages,
)

from app.services.agent.engine.events import (  # noqa: F401
    _sse,
)

from app.services.agent.engine.models import (  # noqa: F401
    _resolve_chat_model,
    _resolve_image_model,
)

from app.services.agent.engine.parsing import (  # noqa: F401
    _parse_json_flexible,
)

from app.services.agent.engine.sessions import (  # noqa: F401
    compact_session,
    create_session,
    delete_session,
    fork_session,
    list_messages,
    list_sessions,
    optimize_prompt,
    search_sessions,
)

from app.services.agent.engine.stream import (  # noqa: F401
    _extract_media_urls,
    chat_stream,
)

from app.services.agent.memory.store import (  # noqa: F401
    _AUTO_REMEMBER_POOL,
    _CONSULTATION_MARKERS,
    _CREATIVE_DECISION_SIGNALS,
    _STRONG_DECISION_WORDS,
    _handle_memory,
    _has_memory_instruction,
    _list_memories,
    _relevant_memories,
    _spawn_auto_remember,
    _spawn_creative_decision_memory,
    auto_remember,
    create_memory_item,
    creative_decision_memory,
    delete_memory_item,
    list_memory_items,
)

from app.services.agent.memory.tfidf import (  # noqa: F401
    _MEMORY_STOPWORDS,
    _cosine_sim,
    _memory_tokenize,
    _tfidf_vector,
    search_memories,
)

from app.services.agent.tools.code import (  # noqa: F401
    _TOOL_CODE_DISPATCH,
    _checkpoint_snapshot,
    _checkpoints_dir,
    _iter_project_files,
    _tool_code_diagnose,
    _tool_code_edit,
    _tool_code_execute,
    _tool_code_list,
    _tool_code_read,
    _tool_code_rollback,
    _tool_code_search,
    _tool_code_write,
)

from app.services.agent.tools.executor import (  # noqa: F401
    _execute_tool,
    _is_builtin_tool_enabled,
    _load_dynamic_tools,
    _parse_args,
    _save_tool_message,
)

from app.services.agent.tools.git import (  # noqa: F401
    _git_commit_execute,
    _git_push_execute,
    _run_git,
    _tool_git_branch,
    _tool_git_diff,
    _tool_git_log,
    _tool_git_status,
)

from app.services.agent.tools.meta import (  # noqa: F401
    _AGENT_ROLES,
    _BUILTIN_PLUGINS,
    _MCP_COMMAND_RE,
    _PLUGIN_MODES,
    _ensure_builtin_plugins,
    _load_enabled_rules,
    _load_plugin_prompts,
    _load_role_configs,
    _validate_mcp_command,
    create_mcp_server,
    create_plugin,
    create_role,
    create_rule,
    delete_mcp_server,
    delete_plugin,
    delete_role,
    delete_rule,
    list_mcp_servers,
    list_plugins,
    list_roles,
    list_rules,
    test_mcp_server,
    update_mcp_server,
    update_plugin,
    update_role,
    update_rule,
)

from app.services.agent.tools.shell import (  # noqa: F401
    _SENSITIVE_PATTERNS,
    _TERMINAL_DENYLIST,
    _check_terminal_command,
    _ensure_not_sensitive,
    _resolve_agent_path,
    _tool_file_read,
    _tool_file_write,
    _tool_terminal_execute,
)

from app.services.agent.tools.skill import (  # noqa: F401
    _tool_install_skill,
    _tool_skill_list,
    _tool_skill_view,
    create_skill,
    delete_skill,
    list_skills,
    update_skill,
)

from app.services.agent.tools.specs import (  # noqa: F401
    _TOOLS,
)

from app.services.agent.tools.web import (  # noqa: F401
    _SEARCH_CACHE_MAX,
    _SEARCH_CACHE_TTL,
    _SEARCH_UA,
    _format_github_results,
    _format_search_results,
    _query_grams,
    _search_baidu,
    _search_bing,
    _search_cache,
    _search_cache_lock,
    _search_tavily,
    _tool_github_search,
    _tool_web_fetch,
    _tool_web_search,
    web_search_with_cache,
)
