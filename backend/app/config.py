from dotenv import load_dotenv
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 把 .env 注入 os.environ，供 ProviderRegistry.resolve_key(api_key_ref) 取密钥。
# pydantic-settings 只读进 settings 对象，不会写入 os.environ，需显式 load。
load_dotenv()

# 项目根目录：app/config.py → app → backend → 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """运行时配置。从 .env 读取，密钥不入库不入 Git。"""

    database_url: str
    redis_url: str
    agnes_api_key: str = Field(default="", alias="AGNES_API_KEY")
    agnes_base_url: str = Field(default="https://apihub.agnes-ai.cn/v1", alias="AGNES_BASE_URL")
    https_proxy: str | None = Field(default=None, alias="HTTPS_PROXY")
    media_dir: str = Field(default="backend/data/media", alias="MEDIA_DIR")
    export_dir: str = Field(default="backend/data/exports", alias="EXPORT_DIR")
    static_base_url: str = Field(default="http://localhost:8000/static", alias="STATIC_BASE_URL")

    # Celery 轮询策略
    celery_poll_interval_default: int = 5
    celery_video_poll_interval: int = 10
    celery_video_timeout: int = 1800

    # 画布导演台模式（2026-08-29）：mock=离线模拟（166 关闭时开发验证）；real=真实提交 166 ComfyUI（166 开机并安装 MiniMax H3 Director 插件后切换）
    director_mode: str = Field(default="mock", alias="DIRECTOR_MODE")

    # 视频出片后自动「LTX 原生精修（只精修不放大）」（2026-08-27，E2A 参数）
    # 2026-08-27 实证：LTX 用 H3 视频重渲在 480p/720p 中远景人脸上是净负优化
    # （重绘脸细节被洗软 + 引入高频噪声塑料感，见 H3资源库 09 修正节 + 踩坑 40/41），
    # 故默认 resolutions 为空 = 对所有草稿档关闭；需要时用 .env
    # VIDEO_REFINE_RESOLUTIONS 显式开启（如 "720p" 单档实验）。
    video_refine_enabled: bool = Field(default=True, alias="VIDEO_REFINE_ENABLED")
    video_refine_resolutions: str = Field(default="", alias="VIDEO_REFINE_RESOLUTIONS")

    # P3 BGM / 音效
    musicgen_url: str = Field(default="http://localhost:9881", alias="MUSICGEN_URL")
    freesound_api_key: str = Field(default="", alias="FREESOUND_API_KEY")

    # 创作助手联网搜索（可选）：配置后走 Tavily 高质量搜索，否则用 Bing 国内版兜底
    search_api_key: str = Field(default="", alias="SEARCH_API_KEY")

    # 创作助手真实环境操作能力开关（对齐 TraeWork，默认启用；可在 .env 关闭）
    agent_terminal_enabled: bool = Field(default=True, alias="AGENT_TERMINAL_ENABLED")
    agent_file_enabled: bool = Field(default=True, alias="AGENT_FILE_ENABLED")
    agent_browser_enabled: bool = Field(default=True, alias="AGENT_BROWSER_ENABLED")
    # 完整编码能力开关（P8：code_* 探索/编辑/沙箱/诊断/回滚 + git_* 结构化操作）
    agent_code_enabled: bool = Field(default=True, alias="AGENT_CODE_ENABLED")
    # 文件/终端能力允许访问的根目录（默认项目根；终端工具默认工作目录）
    agent_workdir: str = Field(default=str(_PROJECT_ROOT), alias="AGENT_WORKDIR")
    # 终端命令超时（秒）
    agent_terminal_timeout: int = Field(default=60, alias="AGENT_TERMINAL_TIMEOUT")

    # 安全：API 访问令牌（可选）。为空则不启用鉴权（本地单机开发保持开箱即用）；
    # 配置后所有 /api 请求（health 除外）需带 `Authorization: Bearer <token>` 或
    # `X-API-Key: <token>`，用于对外/局域网暴露时防止未授权调用（含 MCP/git 等高危接口）。
    api_auth_token: str = Field(default="", alias="API_AUTH_TOKEN")
    # CORS 允许来源（逗号分隔）。为空时默认只允许本地开发源；生产应显式配置前端域名。
    cors_origins: str = Field(default="", alias="CORS_ORIGINS")

    # 去AI味写作工作流（2026-08-24）：生成后审校档位 off|auto|strict，见 docs/去AI味写作规范.md
    humanize_pass: str = Field(default="auto", alias="HUMANIZE_PASS")
    # 默认文风档位：网文爽感|生活流|电影感（生成未显式指定时使用）
    writing_style_default: str = Field(default="网文爽感", alias="WRITING_STYLE_DEFAULT")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    @field_validator("media_dir", "export_dir", "agent_workdir", mode="after")
    @classmethod
    def _to_absolute(cls, v: str) -> str:
        """相对路径按项目根解析为绝对路径，避免不同进程 cwd 不一致导致找不到文件。"""
        p = Path(v)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return str(p)


settings = Settings()
