"""项目分镜提示词批量预热（2026-08-24 新增）：

项目生成（adapt_script / adapt_script_direct）落库成功后由任务自动派发，
后台逐个确保每个分镜的「英文六段式 H3 增强缓存」就绪（ensure_enhanced_prompt
target="video", lang="en"），使后续出片直接命中结构化缓存：
- 出片前不再现场等 LLM 增强（生成耗时更短、体验更稳）
- 出片质量统一（同一套英文六段式基线），为「源头提质」闭环兜底

边界说明：
- 只补尚无英文缓存的分镜（缓存键 target=video + lang=en → *_len*）；
  已有 _len 缓存直接跳过，避免重复消耗 LLM。
- 出片链路若带参考图（minimax_ref 模型传 ref_labels），缓存键带 _r 指纹，
  与预热的基础缓存不同，出片前会按需再生成一次带参考图指代的版本——预热
  仍保证「无参考图/通用场景」命中，多数分镜受益。
- 单镜失败不中断整批（ensure 内部 LLM 失败会回退原始描述）。
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select

from app.database import SessionLocal
from app.models.project import Episode
from app.models.segment import Segment
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 预热并发上限（每镜独立 DB 会话，受 provider 限流约束；可按集群吞吐调整）。
_PREWARM_WORKERS = 4


@celery_app.task(name="prewarm_project_enhance", bind=True)
def prewarm_project_enhance(self, project_id: str, model_id: str | None = None) -> dict:
    """对项目全部分镜批量预热英文六段式 H3 增强缓存。"""
    from app.models.project import Project
    from app.services.prompt_enhance_service import ensure_enhanced_prompt

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            logger.warning("[prewarm] 项目不存在: %s", project_id)
            return {"project_id": project_id, "segments": 0, "done": 0, "skipped": 0, "failed": 0}
        segs = list(
            db.scalars(
                select(Segment)
                .join(Episode, Episode.id == Segment.episode_id)
                .where(Episode.project_id == project.id)
                .order_by(Episode.index.asc(), Segment.index.asc())
            ).all()
        )
        total = len(segs)
        done = skipped = failed = 0

        # 2026-08-31：预热改为有界并发（SQLAlchemy 会话非线程安全，每镜独立 Session）。
        # 此前串行逐镜会把整份 ~4K token 的六段式模板重复发送，几十镜项目预热显著偏慢；
        # 并发可大幅缩短整项目预热时间。并发上限 _PREWARM_WORKERS，兼顾 provider 限流。

        def _process_one(seg_id: str) -> str:
            """单镜预热（工作线程内独立会话），返回 done/skip/fail 状态。"""
            from app.services.prompt_enhance_service import ensure_enhanced_prompt

            wdb = SessionLocal()
            try:
                wseg = wdb.get(Segment, seg_id)
                if wseg is None:
                    return "fail"
                # 已有英文视频缓存则跳过（lang=en → 缓存键尾缀 _len）
                if wseg.enhanced_prompt and wseg.enhanced_target and "_len" in wseg.enhanced_target:
                    return "skip"
                ensure_enhanced_prompt(wdb, wseg, project, target="video", lang="en")
                return "done"
            except Exception as e:  # noqa: BLE001 单镜失败不中断整批
                logger.warning("[prewarm] project=%s seg=%s 预热失败: %s", project_id, seg_id, e)
                wdb.rollback()
                return "fail"
            finally:
                wdb.close()

        jobs = [seg.id for seg in segs]
        completed = 0
        with ThreadPoolExecutor(max_workers=_PREWARM_WORKERS) as pool:
            futures = {pool.submit(_process_one, sid): sid for sid in jobs}
            for fut in as_completed(futures):
                status = fut.result()
                completed += 1
                if status == "done":
                    done += 1
                elif status == "skip":
                    skipped += 1
                else:
                    failed += 1
                if completed % 5 == 0:
                    logger.info(
                        "[prewarm] project=%s 进度 %d/%d (done=%d skip=%d fail=%d)",
                        project_id, completed, total, done, skipped, failed,
                    )
        logger.info("[prewarm] project=%s 完成: total=%d done=%d skip=%d fail=%d",
                    project_id, total, done, skipped, failed)
        return {"project_id": project_id, "segments": total, "done": done, "skipped": skipped, "failed": failed}
    except Exception as e:  # noqa: BLE001
        logger.exception("[prewarm] project=%s 异常: %s", project_id, e)
        return {"project_id": project_id, "error": str(e)}
    finally:
        db.close()
