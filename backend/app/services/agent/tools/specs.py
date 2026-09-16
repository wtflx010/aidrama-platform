"""工具注册层 · 内置工具 schema（OpenAI 兼容 function calling 定义）。

2026-08-28：智能体剧本创作按「四阶段确认流」执行，每个阶段都必须停下来等待
用户确认，禁止自动连跑下一阶段（服务端对「大纲先确认」有硬校验，未确认时
write_script 会被拒绝执行）：
  ① plan_script_outline      规划集纲（同步预览，不建文档、不提交写作）→ 等用户确认「大纲没问题」
  ② write_script             提交后台逐集写作（必须已确认大纲）         → 等剧本写完并确认「剧本没问题」
  ③ generate_shot_preview    按剧本生成分镜预览（必须剧本已写完）       → 等用户确认「分镜没问题」
  ④ generate_poster_and_project  生成封面 + 创建项目（复用已确认分镜预览）→ 流程收尾
其余工具（生图/生视频/联网/代码/文件/终端/浏览器/Git/TTS/画布/技能/MCP 等）一律移除。
"""

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "plan_script_outline",
            "description": "【四阶段第①步·大纲先行】根据用户题材/标题规划分集集纲（同步，只生成大纲预览，不创建剧本文档、不提交后台写作）。调用后必须停下，把返回的集纲预览原样展示给用户，等待用户确认「大纲没问题」；用户确认后的下一轮才可调用 write_script。严禁在本步调用 write_script / generate_shot_preview / generate_poster_and_project，严禁在同一次请求里连跑多个阶段。分集集数可从用户措辞解析（如「写10集」「每集…」）；默认 10 集。续写时带 novel_id，会严格延续前情扩充集纲。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "短剧标题"},
                    "brief": {"type": "string", "description": "题材设定/一句话故事描述（如「都市逆袭：社畜觉醒系统后逆袭打脸」）"},
                    "episodes": {"type": "integer", "description": "集数（默认10，可1~100）"},
                    "genre": {"type": "string", "description": "题材类型（可选，如：都市异能、古风权谋、仙侠）"},
                    "style_mode": {"type": "string", "enum": ["网文爽感", "生活流", "电影感"], "description": "文风档位（可选，默认网文爽感）：网文爽感=节奏快反转密；生活流=日常真实克制；电影感=视听语言优先"},
                    "novel_id": {"type": "string", "description": "续写时传入已有剧本 id（可选）：在其既有集纲上扩充新集，严格延续前情"},
                },
                "required": ["title", "brief"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_script",
            "description": "【四阶段第②步·写剧本】在用户确认大纲后才调用：把已确认的集纲逐集生成场次级完整剧本（场景/动作/对白/旁白），写入剧本库。必须已先调用 plan_script_outline 且用户确认过（此步需传 confirm_outline=true）。用户确认的表达包括：明示确认（如「没问题/确认/可以/就按这个」）或看完大纲后的延续指令（如「生成完整剧本/开始写/直接写」）。提交后必须停下，等待后台写作完成并向用户展示剧本成稿，等用户确认「剧本没问题」；确认后才可调用 generate_shot_preview。严禁在本步生成分镜/封面或创建项目，严禁未经确认就继续（服务端会拒绝）。对话里已细化并确认过完整剧本时，可把正文经 full_script 参数直写落库（唯一无需大纲确认的路径）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "短剧标题（与 plan_script_outline 一致）"},
                    "brief": {"type": "string", "description": "题材设定/一句话故事描述（与 plan_script_outline 一致）"},
                    "episodes": {"type": "integer", "description": "集数（默认10，可1~100；剧本已存在时表示「追加的集数」，服务端会自动累加到已有集数之上）"},
                    "genre": {"type": "string", "description": "题材类型（可选）"},
                    "style_mode": {"type": "string", "enum": ["网文爽感", "生活流", "电影感"], "description": "文风档位（可选，默认网文爽感）"},
                    "novel_id": {"type": "string", "description": "续写时传入已有剧本 id（可选）：复用集纲从断点继续追加新集"},
                    "full_script": {"type": "string", "description": "对话里已确认的「完整剧本」正文（可选，直写路径）：原样写入剧本库，不重新生成；仅当你已在对话中细化并确认过完整正文时才传，否则不传本参数、由服务端按已确认大纲生成。"},
                    "confirm_outline": {"type": "boolean", "description": "必须为 true：表示用户已确认 plan_script_outline 生成的大纲。false/缺失会被服务端拒绝。"},
                },
                "required": ["title"],
                "dependencies": {"full_script": ["brief"]},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_shot_preview",
            "description": "【四阶段第③步·分镜预览】在剧本写完（后台逐集写作完成）且用户确认过剧本后才调用：优先复用你在对话中已按「分镜N-标题（X秒）」格式写好的分镜（解析直落入库，不重新生成）；仅当正文里没有该格式分镜时，才由系统按剧本自动生成（MiniMax H3 规范：镜头数量对齐目标总时长、每镜不超 10 秒、以中景/近景/特写为主），写入剧本库。调用后必须停下，向用户展示分镜概览（幕数/镜数/总时长），等待用户确认「分镜没问题」；确认后才可调用 generate_poster_and_project，严禁直接生成封面或创建项目。剧本未写完时本工具会被服务端拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "novel_id": {"type": "string", "description": "剧本 id（write_script 已返回 novel_id；未传时自动取最近一本有正文的剧本）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_poster_and_project",
            "description": "【四阶段第④步·封面+创建项目】在用户确认分镜后才调用：① 后台生成剧本封面；② 按已确认的分镜预览创建项目（复用预览落库，不重新改写镜头，内容与预览完全一致）。返回项目概况（幕数/镜数/时长/资产）与封面状态。这是四阶段最后一步。",
            "parameters": {
                "type": "object",
                "properties": {
                    "novel_id": {"type": "string", "description": "剧本 id（可选，默认最近一本有正文的剧本）"},
                },
                "required": [],
            },
        },
    },
]
