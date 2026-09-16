"""场景多视角生成任务（单张多格合一图，2026-08-12 重构 v4）。

用户要求（2026-08-12）：场景多视角为**单张图多视角**，不要拼接图；
且生成图必须包含完整六视角（此前后视缺失）。

结论链（均实测）：
- v1 封面单参考：模型把背面格画成正面（front vs back 相似度 0.88）。
- v2 多参考（封面+俯视+背面+左右并排）：辅助参考可生成真背面（16:9），
  但网格阶段模型仍被参考图锚定，背面格依旧画成正面。
- v3 无参考 txt2img：模型能执行"相机旋转180度"画出真背面，但成功率仅
  ~1/3（随机性），且余弦相似度无法区分"真背面/趋同正面"（真背面也 0.89）。

v8 方案（2026-08-23）：**Power「严格锚定封面」单次生成直接采用**。
- 两阶段：封面 img2img → 俯视锚点 → 以俯视图为唯一参考重建六格（v9 实测单参考
  俯视可区分视角；封面直接作参考会引发背面趋同，故不用封面本身做网格参考）。
- 2026-08-23 起取消 QC 质检（原 v4/v5 的视觉模型投票选优 / POV 区分度判定 /
  相似度排序 / 按质检结果多轮重试均已移除）：不再调用视觉模型判定，
  一次出图直接作为最终场景多视角图。
场景一致性由 LLM 扩写（expanded_description）承载。产物为模型原生单张图。

产出 asset.scene_sheet_url。
"""
import logging

from app.database import SessionLocal
from app.models.asset import Asset
from app.models.media import MediaStatus
from app.models.model_config import Model
from app.models.task import Task, TaskStatus
from app.providers.base import ImageOpts
from app.providers.comfyui import _build_flux2_klein_9b_gguf_template
from app.providers.errors import map_to_chinese
from app.providers.registry import ProviderRegistry
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_NEGATIVE = (
    "text, watermark, signature, logo, multiple people, persons, silhouettes, human figure, "
    "deformed hands, extra fingers, "
    "blurry, lowres, jpeg artifacts, fisheye distortion, isometric view, blueprint, "
    "labels, captions, numbers, annotations, "
    "different scene, different location, different environment, unrelated place, "
    "uneven grid, overlapping panels, missing panels, "
    "anime, cartoon, illustration, stylized, 3d render, cgi, cel shading, chibi, "
    "flat illustration, flat colors, line art, lineart, outline art, sketch, "
    "pencil sketch, storyboard, storyboard frame, comic style, painted look, "
    "hand drawn, drawing, concept art, matte painting, blueprint, schematic"
)

# ─── 场景类型判定（室内/室外）──────────────────────────────
_INDOOR_WORDS = [
    "room", "office", "apartment", "living", "bedroom", "kitchen",
    "bathroom", "studio", "interior", "hall", "corridor", "lobby",
    "ward", "classroom", "dormitory", "suite", "study", "meeting",
    "indoors", "inside", "indoor", "house", "home", "villa",
    "会议室", "房间", "办公室", "公寓", "客厅", "卧室", "厨房", "卫生间", "包厢",
    "套间", "书房", "走廊", "大厅", "大堂", "室内", "工作室", "出租屋", "安全屋",
    "教室", "宿舍", "厂房", "仓库", "病房", "别墅", "家",
]
_OUTDOOR_WORDS = [
    "street", "avenue", "boulevard", "highway", "alley", "plaza", "rooftop",
    "garden", "park", "parking", "entrance", "skyline", "dock", "pier",
    "bridge", "outdoor", "courtyard", "facade", "road", "campus",
    "temple", "temple courtyard", "veranda", "corridor", "gallery", "pavilion",
    "wilderness", "desolate", "ruin", "graveyard", "forecourt", "atrium",
    "寺院", "寺庙", "古寺", "兰若寺", "庭院", "天井", "回廊", "长廊", "廊下", "殿",
    "庙", "古宅", "老宅", "大院", "荒郊", "郊外", "荒野", "枯树", "石狮", "残垣",
    "街", "广场", "天台", "花园", "公园", "停车场", "车库", "门口", "天际线", "码头",
    "河边", "桥上", "巷子", "露天", "户外", "大厦", "大楼", "楼",
]
# 强室内/室外词：命中即强烈偏向（权重 2）
_STRONG_INDOOR = [
    "bedroom", "apartment", "living room", "conference room", "meeting room",
    "bathroom", "kitchen", "dormitory", "classroom", "lobby", "hallway",
    "rental room", "indoors", "indoor",
    "卧室", "公寓", "会议室", "出租屋", "室内",
]
_STRONG_OUTDOOR = [
    "street", "alley", "highway", "plaza", "parking lot", "rooftop", "skyline",
    "temple", "temple courtyard", "veranda", "courtyard", "garden", "wilderness",
    "寺院", "寺庙", "古寺", "兰若寺", "庭院", "天井", "回廊", "长廊", "廊下", "殿",
    "街道", "广场", "天台", "露天", "户外",
]


def _classify_space(*texts) -> str:
    """合并多段文本（中文 description + 英文扩写）判断室内/室外。

    用词边界匹配避免子串误命中（"20-square-meter" 不应命中 square、
    "towering" 不应命中 tower）；强室内/室外词权重翻倍。
    """
    import re
    desc = " ".join(t for t in texts if t).lower()

    def _has_cjk(s):
        return any("\u4e00" <= ch <= "\u9fff" for ch in s)

    def _hit(w):
        # 中文词：连续 CJK 串下 \b 边界失效（\b 在相邻中文字间不成立），直接用子串包含；
        # 英文词：保留词边界，避免 "towering" 误命中 "towards"/"20-square" 等子串。
        return (re.escape(w) in desc) if _has_cjk(w) else re.search(rf"\b{re.escape(w)}\b", desc)

    def hits(words):
        return sum(1 for w in words if _hit(w))

    score = (
        hits(_INDOOR_WORDS) + 2 * hits(_STRONG_INDOOR)
        - hits(_OUTDOOR_WORDS) - 2 * hits(_STRONG_OUTDOOR)
    )
    # 2026-08-30+ 修复：score==0（无室内/室外证据）不再默认 indoor。
    # 此前空转 score==0 → "room"（space_word），把户外庭院/古建场景硬拽成室内房间。
    # 归 outdoor→"place"（中性），仅当有明确室内证据（score>0）才判 indoor。
    return "indoor" if score > 0 else "outdoor"


# ─── POV 机位组系统（2026-08-18 v5 重构，替代固定六视角）────────────
# 用户的视角模型：多视角 =「人物站位(P) + 朝向(D) + 机位参数(h/pitch/焦距/景别)」，
# 每个视角用"一个人站在那里看到什么"的第一人称空镜描述（画面本身无人，是代入式手感）。
# 默认机位组（室内/室外各 6 格）：5 个叙事 POV 机位 + 1 个俯视锚点格（布局/唯一性锚）。
# 可被导演 LLM 按剧本推断的机位组覆盖（见 _infer_shots_from_script），也可被 GUI 覆盖（P2）。
_POV_SHOTS_OUTDOOR: list[dict] = [
    # 2026-08-30 user six-camera OUTDOOR group.
    {
        "name": "far_wide",
        "view_text": (
            "Panel 1 (top-left): FAR WIDE view - camera far back at the far end of the "
            "outdoor scene, horizontal wide-angle at eye level, the entire place in one "
            "wide frame: the street or square, buildings and open area fully shown."
        ),
    },
    {
        "name": "side_shift",
        "view_text": (
            "Panel 2 (top-center): SIDE SHIFT view - camera moved horizontally to one "
            "side of the outdoor scene at eye level, exposing the lateral structure: "
            "side facades, the edges of the street or square and their depth."
        ),
    },
    {
        "name": "oblique_top",
        "view_text": (
            "Panel 3 (top-right): OBLIQUE TOP view - camera risen to about 45 degrees "
            "above the outdoor scene, looking diagonally down: the whole street or square "
            "plan in perspective, rooftops and open ground visible."
        ),
    },
    {
        "name": "low_rise",
        "view_text": (
            "Panel 4 (bottom-left): LOW RISE view - camera near the ground with a small "
            "upward tilt, looking up at the upper part of the outdoor space: the building "
            "tops, towers and skyline rising above the low camera."
        ),
    },
    {
        "name": "corner_diagonal",
        "view_text": (
            "Panel 5 (bottom-center): DIAGONAL CORNER view - camera in a corner of the "
            "street or square, diagonal perspective across the outdoor space: two rows of "
            "buildings converge to the far corner for depth."
        ),
    },
    {
        "name": "rear_offset",
        "view_text": (
            "Panel 6 (bottom-right): REAR OFFSET view - camera at the rear side at an "
            "offset, looking back across the whole outdoor place: the back sides of the "
            "buildings, the far edge and the full construction seen from behind."
        ),
    },
]


_POV_SHOTS_INDOOR: list[dict] = [
    # 2026-08-30 user six-camera indoor group.
    {
        "name": "far_wide",
        "view_text": (
            "Panel 1 (top-left): FAR WIDE view - camera at the far end of the room, "
            "horizontal wide-angle at eye level, the ENTIRE interior in one frame: "
            "all walls, all furniture, floor and ceiling shown."
        ),
    },
    {
        "name": "side_shift",
        "view_text": (
            "Panel 2 (top-center): SIDE SHIFT view - camera moved horizontally to one "
            "side of the room at eye level, exposing the lateral structure: side walls, "
            "side facades of furniture and their depth."
        ),
    },
    {
        "name": "oblique_top",
        "view_text": (
            "Panel 3 (top-right): OBLIQUE TOP view - camera risen to about 45 degrees "
            "above the room, looking diagonally down to survey the whole space: the "
            "entire floor plan in perspective, furniture tops visible."
        ),
    },
    {
        "name": "low_rise",
        "view_text": (
            "Panel 4 (bottom-left): LOW RISE view - camera near the floor with a slight "
            "upward tilt, looking up at the upper part of the room: the ceiling, "
            "furniture tops and the vertical structure rising above the low camera."
        ),
    },
    {
        "name": "corner_diagonal",
        "view_text": (
            "Panel 5 (bottom-center): DIAGONAL CORNER view - camera in a corner of the "
            "room, using diagonal perspective across the space: two walls converge to "
            "the opposite corner, furniture lines run diagonally for depth."
        ),
    },
    {
        "name": "rear_offset",
        "view_text": (
            "Panel 6 (bottom-right): REAR OFFSET view - camera at the rear side at an "
            "offset, looking back across the entire interior: the far side of furniture, "
            "the back walls and the full inner construction seen from behind."
        ),
    },
]


def _render_pov_grid_prompt(
    shots: list[dict],
    space_word: str,
    scene_desc: str,
    top_ref_url: str | None = None,
    cover_ref_url: str | None = None,
) -> str:
    """渲染单张 3x2 网格 prompt：每格 = POV 机位文本（站位+朝向+机位参数）。

    top_ref_url 为空 → 无参考 txt2img（默认路径，与 v4 相同，规避参考锚定趋同）；
    非空 → Power 模式：以俯视锚点图为唯一参考重建同一布局各视角（reference 继承布局）。
    """
    # Power 模式（top_ref_url 非空）：每格 view_text 后追加「空间锁定」后缀——机位只能
    # 在该目标空间内部移动，严禁穿过门/窗/走廊/通道进入其它房间（2026-08-19 收紧）：
    # 旧版参考图压不住导演 view_text 的跨空间描述，废弃柴房曾漂移出厅堂/走廊/卧室。
    _space_lock = (
        f" All camera positions stay strictly INSIDE this one {space_word} of the reference - "
        "never pass through any doorway, window, corridor or passage to another area."
    ).format(space_word=space_word)
    panel_lines = "\n\n".join(
        s["view_text"].format(space_word=space_word)
        + (_space_lock if (top_ref_url or cover_ref_url) else "")
        for s in shots
    )
    pov_names = " / ".join(s["name"] for s in shots)
    top_only = ""  # 六机位组无 top_anchor 锚格
    if cover_ref_url:
        # 2026-08-30 cover 模式：封面为人类平视照片，六格为场景内人类视角。
        # 避免俯视参考把整体拉成俯视（用户反馈"都是俯视图"）。
        head = (
            "The reference image is a real photograph of the {space_word} taken at human eye "
            "level. Reconstruct THE EXACT SAME {space_word}, then render the SIX perspectives "
            "below as the views a person standing inside this space would see from each "
            "position. Same furniture, same lighting, same materials and same "
            "photorealistic look as the reference - never a different place, never open "
            "into another room, corridor, balcony or outside area.\n\n" +
            "COVER SCENE LOCK (ABSOLUTE PRIORITY): The reference photo is the ONE AND ONLY "
            "definitive source of this space. Every one of the SIX panels MUST show THE EXACT "
            "SAME {space_word} as the reference photo: identical architecture, identical "
            "furniture in IDENTICAL positions, identical wall / floor / ceiling, identical "
            "materials, colors, textures and identical lighting. Absolutely NO object may be "
            "added, removed, moved, rearranged, resized or repainted from the reference in any "
            "panel, and no panel may introduce a different room, different layout or different "
            "furnishings.\n\n" +
            "CRITICAL CAMERA DIFFERENCE (ABSOLUTE PRIORITY): the SIX panels must be SIX "
            "CLEARLY DIFFERENT camera setups of that same scene - only the scene content above "
            "is locked, the camera is NOT. Each panel MUST follow its own view description "
            "below (far wide / side shift / oblique top / low rise / corner diagonal / rear "
            "offset) and show a DISTINCT vantage point: different distance, height, pitch, yaw "
            "and framing. Far-wide pulls far back, side-shift shifts laterally, oblique-top "
            "looks down from about 45 degrees, low-rise tilts up toward upper structures, "
            "corner-diagonal shoots across a diagonal, rear-offset looks back from the far side. "
            "Do NOT make all six panels look like the cover photo - the cover photo is only the "
            "scene reference, its viewpoint must NOT be reused in more than one panel.\n\n" +
            "STYLE (CRITICAL): photorealistic cinematic photography, real-world materials, "
            "natural realistic lighting, film camera look, detailed texture, depth and "
            "shadows, exactly like a still from a real camera. NOT flat illustration, NOT "
            "line art, NOT cartoon, NOT anime, NOT 3D render, NOT blueprint.\n\n"
        ).format(space_word=space_word)
    elif top_ref_url:
        head = (
            "The reference image below is the TOP-DOWN layout map of ONE scene (showing the "
            f"{space_word} and the spatial positions of all its elements). Reconstruct THE "
            "EXACT SAME scene from that layout, then render the SIX perspectives below "
            "exactly as described. Keep the same layout, same buildings/furniture, same "
            "lighting and same art style as the reference - never invent a different place, "
            "never open into any other room, corridor, balcony or outside area. The "
            f"reference is the ONLY {space_word} that exists in this image: ALL six "
            f"camera positions stand INSIDE this one {space_word}, showing only this "
            "space and its furniture.\n\n"
            "STYLE (CRITICAL): photorealistic cinematic photography — realistic materials, "
            "real-world materials, natural realistic lighting, true film camera look, "
            "detailed texture of wood/plaster/fabric, depth and shadows, exactly like a "
            "photograph of a real place. NOT flat illustration, NOT line art, NOT cartoon, "
            "NOT anime, NOT storyboard sketch, NOT schematic, NOT blueprint, NOT 3D render, "
            "NOT game concept art. The output must look like a still frame captured by a "
            "real camera.\n\n"
        )
    else:
        head = (
            "Create a SINGLE multi-panel scene reference sheet of ONE {space_word}: a 3x2 "
            "grid layout (three panels in the top row, three panels in the bottom row). "
            "All six panels show THE EXACT SAME {space_word}, only from different human "
            "perspectives and camera setups.\n\n"
            "The {space_word}: {scene_desc}\n\n"
            "STYLE: photorealistic cinematic photography — realistic materials, real-world "
            "lighting, film camera look, natural perspective. NOT anime, NOT cartoon, NOT "
            "illustration, NOT 3D render.\n\n"
        ).format(space_word=space_word, scene_desc=scene_desc)
    critical = (
        f"\n\nCRITICAL: all six panels MUST be the same {space_word} — identical layout, "
        "identical details, identical lighting and identical art style. Each panel is a "
        f"DIFFERENT perspective and camera setup: {pov_names}. {top_only} Each panel MUST "
        "use a distinct camera (different distance/height/pitch/yaw/framing per its own view "
        "label): no two panels may reuse the same vantage point, NEVER converge to the same "
        "camera, and in COVER mode do NOT simply repeat the cover photo's viewpoint. All "
        "panels are empty scenes with NO people, NO text, NO labels.\n\n"
        "SPACE LOCK: the image contains exactly ONE location — the {space_word} from the "
        "reference. Every camera stays inside that single space; no panel may show any "
        "other room, hallway, corridor, staircase, adjoining chamber, balcony, garden or "
        "street through doorways, windows or passages. If a described view would look "
        "toward a doorway, frame it from inside this space looking at the wall/door "
        "FACING the camera, never a view on the other side.\n\n"
        "PHOTO REALISM LOCK: this is a real photograph of a real place, NOT a drawing. "
        "Render photorealistic textures (wood grain, peeling paint, plaster, dust, fabric), "
        "natural photographic lighting and true-to-life colors with camera depth of field. "
        "Flat colors, outlined shapes, hand-drawn or painted look, comic/storyboard style "
        "are FORBIDDEN.\n\n"
        "Clean 3x2 grid with thin panel dividers, uniform panel sizes, no text, no labels, "
        "no arrows."
    ).format(space_word=space_word)
    prompt = head + "Grid layout (left to right):\n\n" + panel_lines + critical
    if cover_ref_url:
        # 收尾强约束（2026-08-30+）：封面场景绝对不改，只在场景内部换机位。
        prompt += (
            "\n\nCOVER SCENE ABSOLUTE LOCK: the reference image's scene is the ONLY scene in "
            "this whole image. All six panels are the SAME scene photographed from six "
            "different camera positions inside it. Do NOT redesign, alter, move, add, remove, "
            "rearrange, recolor or rebuild ANY element of the cover scene - the architecture, "
            "furniture and their exact placement, materials, colors, textures and lighting in "
            "every panel MUST match the cover image. The only difference permitted between "
            "panels is the camera position, height and viewing angle. Never change the scene to "
            "make it fit a described camera; instead change the camera to re-frame the exact "
            "same scene."
        )
    return prompt

_BACK_DESC_INDOOR = (
    "the camera stands at the back of the room (the far end opposite the entrance), looking "
    "toward the entrance wall — the entrance door is now in the distance, the furniture is seen "
    "from its rear end"
)
_BACK_DESC_OUTDOOR = (
    "the camera stands behind the building, looking at its rear facade — the back of the "
    "building with back doors and rear windows, the side facing away from the front"
)

# 网格尺寸（Klein 9B 768p 基准 16:9）
_GRID_W, _GRID_H = 1344, 768


@celery_app.task(name="generate_scene_multiview", bind=True)
def generate_scene_multiview(self, task_id: str):
    db = SessionLocal()
    # 防御（2026-08-18）：prefork fork 可能继承父进程 aborted 连接，首行清事务状态，
    # 避免首个查询报 InFailedSqlTransaction（与 celery_app worker_process_init dispose 双保险）
    db.rollback()
    target_id = None
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        target_id = task.target_id
        asset = db.get(Asset, target_id)
        if asset is None:
            return

        model = db.get(Model, task.model_id)
        if model is None:
            raise ValueError("模型不存在")
        provider = ProviderRegistry.for_model(model)

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        asset.status = MediaStatus.running
        db.commit()

        scene_desc = asset.expanded_description or asset.description or asset.name
        space = _classify_space(asset.description or "", asset.expanded_description or "", asset.name or "")
        space_word = "room" if space == "indoor" else "place"
        logger.info("场景 %s 类型判定: %s", asset.id, space)

        # 机位组优先级（2026-08-18 v5）：GUI 显式配置(scene_shots) > 导演 LLM
        # 按剧本推断 > 内置默认机位组（室内/室外）
        shots = None
        if isinstance(asset.scene_shots, list) and len(asset.scene_shots) >= 6 and all(
            isinstance(s, dict) and s.get("view_text") for s in asset.scene_shots
        ):
            shots = asset.scene_shots
        if shots is None:
            shots = _infer_shots_from_script(db, asset, space) or (
                _POV_SHOTS_INDOOR if space == "indoor" else _POV_SHOTS_OUTDOOR
            )
        logger.info("场景 %s 使用机位组: %s", asset.id, [s["name"] for s in shots])

        from app.config import settings
        import os

        asset_dir = os.path.join(settings.media_dir, "assets", str(asset.id))
        os.makedirs(asset_dir, exist_ok=True)
        # ── 生成策略（2026-08-23 起：取消 QC 质检）──
        # 原 v5 Power 流程含视觉模型 POV 区分度判定（_vision_pov_verdict）+ FRONT/BACK
        # 相似度选优 + 按质检结果最多重试 3 轮 + 未达标兜底一次；2026-08-23 用户取消
        # QC 质检后简化为单次生成直接采用：封面 → 俯视锚点（唯一参考）→ 六格一次出图。
        if not asset.cover_url:
            raise ValueError("场景多视角需先生成场景封面（Power 模式以封面为图像锚）")

        # 阶段② 封面（人类平视照片）作唯一参考 + 用户六机位文本，直接出六格。
        # 2026-08-30：去掉俯视锚点中间步骤——俯视图作参考会把六格整体拉成俯视
        # （用户反馈"生成的场景多视角目前都是俯视图"）。封面是正常人类视角，
        # 模型以人眼视角为锚演绎六种机位。
        power_prompt = _render_pov_grid_prompt(
            shots, space_word, scene_desc, cover_ref_url=asset.cover_url,
        )
        power_opts = ImageOpts(
            width=_GRID_W, height=_GRID_H,
            negative_prompt=_NEGATIVE,
            reference_labels=["Image 1: 场景封面（人类平视视角实拍，保持其真实感与材质光照质感；六格为人在该空间内不同位置看到的视角）"],
        )
        power_handle = provider.imageToImage(power_prompt, [asset.cover_url], power_opts)
        power_result = run_with_polling(
            db, task_id, provider, power_handle, poll_interval=5, timeout=900
        )
        if not power_result.imageUrls:
            raise RuntimeError("场景多视角网格生成无输出")
        # 目标文件名固定为 scene_sheet.png（原选优/复制/清临时文件逻辑已随 QC 一并移除）
        local_url = download_to_local(
            power_result.imageUrls[0], subdir=f"assets/{asset.id}",
            filename="scene_sheet.png", task_id=task_id,
        )
        # 清理俯视锚点中间产物（六格已直接落为最终名）
        try:
            os.remove(os.path.join(asset_dir, "scene_sheet_top.png"))
        except OSError:
            pass
        asset.scene_sheet_url = local_url
        asset.status = MediaStatus.succeeded
        asset.error = None
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=local_url, finished_at=now(),
        )
        db.commit()
        logger.info("场景 %s 单张多视角图: %s", asset.id, local_url)
    except TaskCancelledError:
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        if target_id:
            asset = db.get(Asset, target_id)
            if asset:
                asset.status = MediaStatus.failed
                asset.error = msg
                db.commit()
    finally:
        db.close()


# ─── 导演 LLM 机位推断（2026-08-18 P1）────────────────────────
# 用户决策：多视角机位应由剧本/镜头语言决定。当场景被分镜引用时，由导演 LLM
# 依据「场景描述 + 该场景镜头序列」输出 6 格机位组（结构固定：top/front/
# lookback/left/right/wide 各一格，站位/朝向/机位参数可自定义），失败或非
# 结构化输出 → 返回 None（主任务回退内置默认机位组）。
_SHOT_TYPE_ORDER = ["top", "front", "lookback", "left", "right", "wide"]
_SHOT_SLOT_LABEL = {
    "top": "Panel 1 (top-left)",
    "front": "Panel 2 (top-center)",
    "lookback": "Panel 3 (top-right)",
    "left": "Panel 4 (bottom-left)",
    "right": "Panel 5 (bottom-center)",
    "wide": "Panel 6 (bottom-right)",
}

_DIRECTOR_SYSTEM = (
    "你是短剧导演兼掌机摄影师。为某个场景生成 6 格「多视角参考图」的机位组："
    "每格 = 一个第一人称空镜（人物站在场景某处看向某处 + 机位参数），"
    "六格覆盖：俯视top / 正面front / 回望lookback / 左侧left / 右侧right / 远景wide，"
    "各一格。六格必须差异明显、两两不重复。只输出 JSON。"
    "2026-08-19 约束：站位(stance)与看向(direction)必须严格停留在该场景元素内部——"
    "不能写「站到门外/望向窗外/走进隔壁房间/穿过走廊/从走廊看」等跨空间描述；"
    "所有机位都在同一空间内部改变站位与朝向（如在房间或院子内部靠近/远离/转向），"
    "机位差异靠距离、高度、俯仰、景别实现，不靠换空间。"
)
_DIRECTOR_TMPL = (
    "场景：{scene_desc}\n"
    "该场景的关键镜头（JSON 数组）：{shots_desc}\n"
    "根据镜头的景别/机位倾向，为 6 个视角格给出站位站位(stance)、看向(direction)与机位参数"
    "(camera: height/pitch/fov/shot_size)。six格顺序固定为 "
    "{order}，每格必须输出。只输出 JSON：\n"
    '{{"shots":[{{"name":"…","shot_type":"top","stance":"…","direction":"…","camera":{{"height":"1.5m","pitch":"0","fov":"35mm","shot_size":"…"}}}},'
    '{{"name":"…","shot_type":"front",…}}, …]}}'
)


def _shots_to_view_texts(shots: list[dict], space_word: str) -> list[dict] | None:
    """把导演 LLM 的结构化机位组渲染为网格 view_text；结构不完整返回 None。"""
    by_type = {s.get("shot_type"): s for s in shots if s.get("shot_type") in _SHOT_SLOT_LABEL}
    if len(by_type) != len(_SHOT_TYPE_ORDER):
        return None
    out: list[dict] = []
    for t in _SHOT_TYPE_ORDER:
        s = by_type[t]
        cam = s.get("camera") or {}
        cam_str = ", ".join(f"{k} {v}" for k, v in cam.items() if str(v))
        pos = _SHOT_SLOT_LABEL[t]
        view = (
            f"{pos}: {str(s.get('name') or t).capitalize()} view — camera positioned "
            f"{s.get('stance') or 'at that spot'}; first-person perspective "
            f"({cam_str}) {s.get('direction') or 'looking around'}. "
        )
        out.append({"name": str(s.get("name") or t), "view_text": view})
    return out


def _infer_shots_from_script(db, asset, space) -> list[dict] | None:
    """导演 LLM 依据剧本镜头序列推断 6 格机位组；无分镜/失败/不可用 → None。"""
    try:
        from app.models.model_config import ModelType
        from app.models.segment import Segment
        from app.providers.registry import ProviderRegistry
        from app.services.asset_service import _resolve_model

        segs = (
            db.query(Segment)
            # segment.scene_id 是 VARCHAR 列：必须传字符串比较，传 UUID 对象会触发
            # "character varying = uuid" UndefinedFunction（2026-08-18 排查修复）
            .filter(Segment.scene_id == str(asset.id))
            .order_by(Segment.index if hasattr(Segment, "index") else Segment.created_at)
            .limit(10)
            .all()
        )
        shot_lines = []
        for s in segs:
            text = (s.description or "").strip()[:220]
            if text:
                shot_lines.append(text)
        # 判定器假设六格语义结构，导演组也须按固定结构 → 无论镜头多寡都可推断；
        # 但至少要有 1 条镜头描述才有信息量，否则直接用默认组
        if not shot_lines:
            return None

        model = _resolve_model(db, None, ModelType.text, "scenemultiview")
        provider = ProviderRegistry.for_model(model)
        import json
        user = _DIRECTOR_TMPL.format(
            scene_desc=(asset.expanded_description or asset.description or asset.name)[:600],
            shots_desc=json.dumps(shot_lines[:8], ensure_ascii=False),
            order="/".join(_SHOT_TYPE_ORDER),
        )
        data = provider.chat([
            {"role": "system", "content": _DIRECTOR_SYSTEM},
            {"role": "user", "content": user},
        ])
        text = (data["choices"][0]["message"]["content"] or "")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        payload = json.loads(text[start:end + 1])
        raw = payload.get("shots") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            return None
        views = _shots_to_view_texts(raw, space)
        logger.info("场景 %s 导演机位推断 %s 格: %s", asset.id,
                    len(raw), [s.get("name") for s in raw])
        return views
    except Exception as e:
        # 关键：先 rollback 再返回 None——任何 DB 异常都会把 session 事务标记为
        # aborted（InFailedSqlTransaction），不 rollback 会污染任务后续所有 DB 操作
        # （2026-08-18 排查修复：此前的 UndefinedFunction 即因此白爆掉整个任务）
        db.rollback()
        logger.warning("场景 %s 导演机位推断失败，回退默认机位组: %s", asset.id, e)
        return None