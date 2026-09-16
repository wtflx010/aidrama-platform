from celery import Celery
from celery.schedules import schedule
from celery.signals import worker_process_init

from app.config import settings
from app.database import engine

# prefork 池下每个 fork 子进程会继承父进程建立的 SQLAlchemy 连接（连接池按需建，
# fork 前父进程可能已持有连接）。若父进程某连接恰好处于陈腐/aborted 事务状态，
# 子进程复用它时首个查询就报 `InFailedSqlTransaction` —— 标准修法：子进程启动时
# 丢弃继承的连接池，让每个 worker 各自建立全新连接（2026-08-18 排查修复）。
@worker_process_init.connect
def _dispose_engine_on_fork(**kwargs):  # noqa: ANN001
    engine.dispose()

celery_app = Celery(
    "pavo",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.generate_keyframe",
        "app.tasks.generate_video",
        # 2026-08-23：视频超分（480p → 1080p，ComfyUI 逐帧 SR + 本地保真）
        "app.tasks.upscale_video",
        # 2026-08-10：孤儿视频恢复（提交 ComfyUI 后 worker 崩溃 → 自动找回）
        "app.tasks.recover_orphan_video",
        "app.tasks.generate_export",
        # P7 幕级视频（取代逐镜视频）
        "app.tasks.generate_episode_video",
        # 白模故事版（2026-08-10）：动作序列多镜头完整展示（手动触发）
        "app.tasks.generate_action_sequence",
        # AI 视频页签（2026-08-11）：项目级独立视频草稿（文生/首尾帧/参考视频+图片混合）
        "app.tasks.generate_video_draft",
        # P1
        "app.tasks.generate_asset_cover",
        "app.tasks.generate_asset_fourview",
        # 场景多视角（POV 六格合一图，2026-08-10）
        "app.tasks.generate_scene_multiview",
        "app.tasks.generate_voice",
        "app.tasks.batch_keyframes",
        # 画布(生图工作台)批量生成(2026-08-15)
        "app.tasks.canvas_generate",
        # 画布导演台模式(2026-08-29):多段连续生视频
        "app.tasks.canvas_director_generate",
        # 项目页签连续长片(2026-09):整集多段连续整片
        "app.tasks.project_director_generate",
        "app.tasks.batch_videos",
        # P2 批量配音重跑
        "app.tasks.batch_voice",
        # P7 批量资产生成封面
        "app.tasks.batch_asset_covers",
        # P3 小说→剧本 + BGM/音效
        "app.tasks.generate_novel",
        # 剧本海报（write_script_lib 写完后自动派发；2026-08-23 补注册，
        # 此前 unregistered 被 Celery 丢弃致海报永久缺失）
        "app.tasks.generate_novel_poster",
        # 2026-08-24 项目分镜提示词批量预热（生成项目后自动派发，确保英文
        # 六段式缓存就绪、出片直接命中高质量提示词）
        "app.tasks.prewarm_prompt",
        "app.tasks.generate_bgm",
        "app.tasks.generate_sfx",
        # 2026-08-12 分集剧本写作（Agent 触发，逐集生成完整剧本并逐集追加为项目幕）
        "app.tasks.generate_script",
        # P3 任务实时性：卡死任务回收
        "app.tasks.reclaim_stuck_tasks",
        # P8 定时自动化：扫描到期 agent_schedule 并执行
        "app.tasks.agent_schedule_tick",
        # P0-1 成片评估（规则分 + LLM 四维分 + 反哺建议）
        "app.tasks.evaluate_episode",
        # P2-5 多语言配音与字幕导出
        "app.tasks.dub_episode",
        # P1-4 修片工作流（reframe / voice-change / draw-to-video）
        "app.tasks.rework_video",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # 推荐 --pool=threads --concurrency=3（见 scripts/start_celery.sh）：
    # I/O 密集型任务（HTTP 轮询 + 下载）适合线程池，无 fork 开销，避免 macOS spawn 问题。
    # task_acks_late + prefetch=1：任务执行完才 ack，防止线程池中某线程崩溃丢任务。
    # 2026-08-10 恢复卡死回收调度：仅回收卡死状态 + 自动捡回远程已完成的产物，
    # **不触发生视频/生图/任何生成任务**（用户"禁止系统自动触发"仅针对生成类任务）。
    # 若 worker 崩溃导致任务卡 running，此调度自动探测远程 → 已完成则捡回，
    # 未完成/失败则回收为 failed 供用户重试——避免再次出现"视频生完了前端还卡着"。
    beat_schedule={
        "reclaim-stuck-tasks": {
            "task": "reclaim_stuck_tasks",
            "schedule": schedule(run_every=60.0),  # 每 60s 扫描一次
        },
        # P8 定时自动化：每 30s 扫描到期定时任务（cron 最小粒度 1 分钟）
        "agent-schedule-tick": {
            "task": "agent_schedule_tick",
            "schedule": schedule(run_every=30.0),
        },
    },
)

