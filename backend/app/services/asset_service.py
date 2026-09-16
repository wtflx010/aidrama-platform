"""资产业务服务：角色/场景/道具 CRUD + 封面/四视图/场景图生成 + 描述扩写。

2026-08-22 全局资产库：
- Asset 可脱离项目存在（project_id 可空）；项目通过 project_asset 绑定复用
- list_by_project 兼容「归属项目 + 绑定项目」两种来源
- 项目删除仅解绑，资产保留在全局库
"""
import re
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetType, ProjectAsset
from app.models.media import MediaStatus
from app.models.model_config import Model, ModelType
from app.models.project import Project
from app.models.task import Task, TaskStatus, TaskType
from app.providers.base import ImageOpts
from app.providers.registry import ProviderRegistry
from app.schemas.asset import AssetCreate, AssetUpdate
from app.services.keyframe_service import _resolve_model


def project_ids_of(db: Session, asset_id) -> list[str]:
    """资产绑定的项目 id 列表（含归属项目）。"""
    asset = db.get(Asset, asset_id)
    if not asset:
        return []
    ids: list[str] = []
    if asset.project_id:
        ids.append(str(asset.project_id))
    rows = db.scalars(
        select(ProjectAsset.project_id).where(ProjectAsset.asset_id == asset_id)
    ).all()
    for pid in rows:
        if str(pid) not in ids:
            ids.append(str(pid))
    return ids


def list_by_project(db: Session, project_id, type: AssetType | None = None):
    """返回项目可用资产：归属本项目 + 全局库脚本通过绑定关联到本项目的资产。

    排序：created_at ASC + id ASC（保持稳定顺序，编辑不跳位）。
    """
    q = select(Asset).where(
        or_(
            Asset.project_id == project_id,
            Asset.id.in_(
                select(ProjectAsset.asset_id).where(
                    ProjectAsset.project_id == project_id
                )
            ),
        )
    )
    if type:
        q = q.where(Asset.type == type)
    return db.scalars(q.order_by(Asset.created_at.asc(), Asset.id.asc())).all()


def list_library(db: Session, type: AssetType | None = None, project_id=None):
    """全局资产库：所有资产（含全局 + 各项目归属）+ 可选按项目过滤可用资产。"""
    q = select(Asset).order_by(Asset.created_at.desc(), Asset.id.desc())
    if project_id:
        q = q.where(
            or_(
                Asset.project_id == project_id,
                Asset.id.in_(
                    select(ProjectAsset.asset_id).where(
                        ProjectAsset.project_id == project_id
                    )
                ),
            )
        )
    if type:
        q = q.where(Asset.type == type)
    return db.scalars(q).all()


def get(db: Session, asset_id) -> Asset | None:
    return db.get(Asset, asset_id)


def create(db: Session, project_id, payload: AssetCreate) -> Asset:
    """创建资产。project_id 可空（空 = 全局资产生成/手动创建进库）；
    有项目时自动建立绑定（project_asset），保持项目内可见。"""
    pid = payload.project_id if payload.project_id is not None else project_id
    if pid is not None and not db.get(Project, pid):
        raise ValueError("项目不存在")
    asset = Asset(
        project_id=pid, type=payload.type, name=payload.name,
        description=payload.description, status=MediaStatus.pending,
    )
    db.add(asset)
    db.flush()
    if pid is not None:
        bind_asset(db, asset.id, pid)
    db.commit()
    db.refresh(asset)
    return asset


def bind_asset(db: Session, asset_id, project_id) -> None:
    """把资产绑定到项目（全局库 → 项目可用）；去重。"""
    if db.get(Project, project_id) is None:
        raise ValueError("项目不存在")
    exists = db.scalar(
        select(ProjectAsset).where(
            ProjectAsset.asset_id == asset_id,
            ProjectAsset.project_id == project_id,
        )
    )
    if exists is None:
        db.add(ProjectAsset(asset_id=asset_id, project_id=project_id))
        db.commit()


def unbind_asset(db: Session, asset_id, project_id) -> None:
    """解除资产与项目的绑定（项目删除或手动解绑）；资产保留。"""
    row = db.scalar(
        select(ProjectAsset).where(
            ProjectAsset.asset_id == asset_id,
            ProjectAsset.project_id == project_id,
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()


def update(db: Session, asset_id, payload: AssetUpdate) -> Asset:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    if payload.name is not None:
        asset.name = payload.name
    if payload.description is not None:
        asset.description = payload.description
    # 2026-08-11：生图提示词（扩写描述）前端可编辑，保存后供后续生成直接使用
    if payload.expanded_description is not None:
        asset.expanded_description = payload.expanded_description.strip() or None
    db.commit()
    db.refresh(asset)
    return asset


def _remove_bindings(db: Session, asset_id) -> None:
    """删除资产前清除其全部 project_asset 绑定（含归属 project_id 置空），
    避免项目内悬空引用。"""
    db.query(ProjectAsset).filter(ProjectAsset.asset_id == asset_id).delete(
        synchronize_session=False
    )
    asset = db.get(Asset, asset_id)
    if asset is not None and asset.project_id is not None:
        asset.project_id = None


def delete(db: Session, asset_id) -> bool:
    """删除资产（含清理磁盘媒体文件），并清除其项目绑定。

    2026-08-30 需求调整：绑定项目的资产也允许删除（原 2026-08-23 绑定保护已放开）。
    删除时：
    - 清除该资产的全部 project_asset 绑定（项目内不再悬空）;
    - 删除 asset 行;
    - 清理 cover / 四视图 / 角色设定卡 / 场景多视角 / 参考图 / 版本图 / 声线参考音频，
      并整体删除 {media_dir}/assets/{asset_id}/ 目录。

    Returns:
        True 删除成功 / False 资产不存在
    """
    import os
    import shutil

    from app.config import settings
    from app.utils.media import delete_media_file

    asset = db.get(Asset, asset_id)
    if not asset:
        return False

    _remove_bindings(db, asset_id)

    urls: list[str] = []
    for u in (asset.cover_url, asset.character_sheet_url, asset.scene_sheet_url):
        if u:
            urls.append(u)
    for u in (asset.four_view_urls or []):
        if u:
            urls.append(u)
    for s in (asset.states or []):
        if s.get("image_url"):
            urls.append(s["image_url"])
    for u in (asset.reference_images or []):
        if u:
            urls.append(u)
    for v in (asset.art_versions or []):
        if v.get("url"):
            urls.append(v["url"])
    vp = asset.voice_profile or {}
    if vp.get("reference_audio_url"):
        urls.append(vp["reference_audio_url"])

    db.delete(asset)
    db.commit()

    for u in dict.fromkeys(u for u in urls if u):
        try:
            delete_media_file(u)
        except Exception:
            continue
    asset_dir = os.path.join(settings.media_dir, "assets", str(asset_id))
    if os.path.isdir(asset_dir):
        shutil.rmtree(asset_dir, ignore_errors=True)
    return True


def delete_with_media(db: Session, asset_id) -> bool:
    """删除资产并一并清理其生成的磁盘媒体文件（项目删除联动使用）。

    2026-08-24 项目专用美术资产：项目删除时资产随项目删除。
    2026-08-30 实现收敛：与 delete() 相同（绑定项目也可删 + 清绑定 + 清媒体），
    保留函数名以兼容项目删除联动调用。
    """
    return delete(db, asset_id)


def upload_cover_image(db: Session, asset_id, filename: str, data_base64: str) -> Asset:
    """人工上传图片作为资产封面/主图（角色/场景/道具通用）。

    base64 上传（与参考音频一致，避免 python-multipart 依赖），
    保存到 {media_dir}/assets/{asset_id}/，回写 cover_url，状态置 succeeded。
    """
    import base64 as _b64
    import os as _os
    import re as _re
    from pathlib import Path as _Path

    from app.config import settings

    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")

    safe_name = _re.sub(r"[^\w.\-]", "_", filename or "upload.png")
    ext = _Path(safe_name).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError(f"不支持的图片格式: {ext or '(无扩展名)'}（仅支持 png/jpg/jpeg/webp）")

    try:
        img_bytes = _b64.b64decode(data_base64)
    except Exception:
        raise ValueError("base64 解码失败")
    if not img_bytes:
        raise ValueError("图片数据为空")
    if len(img_bytes) > 20 * 1024 * 1024:
        raise ValueError("图片文件过大（最大 20MB）")

    save_dir = _Path(settings.media_dir) / "assets" / str(asset.id)
    save_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"upload_{_os.urandom(8).hex()}{ext}"
    (save_dir / unique_name).write_bytes(img_bytes)

    asset.cover_url = f"{settings.static_base_url}/media/assets/{asset.id}/{unique_name}"
    asset.status = MediaStatus.succeeded
    asset.error = None
    db.commit()
    db.refresh(asset)
    return asset


def _create_asset_task(db: Session, asset: Asset, task_type: TaskType, model: Model) -> Task:
    """建资产生成任务并回填 asset.task_id。

    target_type 用资产具体类型（character/scene/prop），便于任务中心区分展示，
    而非笼统的 "asset"。
    """
    task = Task(
        project_id=asset.project_id, type=task_type,
        target_type=asset.type.value, target_id=asset.id, model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    asset.task_id = task.id
    asset.status = MediaStatus.pending
    asset.error = None
    db.commit()
    db.refresh(task)
    return task


def generate_cover(db: Session, asset_id, model_id=None):
    """角色/场景/道具封面图生成。"""
    from app.tasks.generate_asset_cover import generate_asset_cover

    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    scene_code = "character_cover" if asset.type == AssetType.character else asset.type.value
    model = _resolve_model(db, model_id, ModelType.image, scene_code)
    task = _create_asset_task(db, asset, TaskType.generate_asset_cover, model)
    generate_asset_cover.delay(str(task.id))
    return asset, task


def generate_fourview(db: Session, asset_id, model_id=None):
    """角色四视图生成（正/侧/背/全身）。仅 character 类型。"""
    from app.tasks.generate_asset_fourview import generate_asset_fourview

    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    if asset.type != AssetType.character:
        raise ValueError("仅角色资产支持四视图生成")
    if not asset.cover_url:
        raise ValueError("请先生成角色封面，四视图需以封面为参考")
    model = _resolve_model(db, model_id, ModelType.image, "character_fourview")
    task = _create_asset_task(db, asset, TaskType.generate_asset_fourview, model)
    generate_asset_fourview.delay(str(task.id))
    return asset, task


def generate_scene_image(db: Session, asset_id, model_id=None):
    """场景图生成。"""
    from app.tasks.generate_asset_cover import generate_asset_cover

    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    model = _resolve_model(db, model_id, ModelType.image, "scene")
    task = _create_asset_task(db, asset, TaskType.generate_asset_cover, model)
    generate_asset_cover.delay(str(task.id))
    return asset, task


def generate_scene_multiview(db: Session, asset_id, model_id=None, shots=None):
    """场景多视角生成（单张 3x2 六视角合一图）。仅 scene 类型。

    以封面为参考，Flux.2 Klein 9B 生成单张多视角网格图（2026-08-18 v5：
    POV 机位组驱动，默认组 / 导演 LLM 推断 / GUI 配置覆盖三层）。
    传入 shots 时持久化到 asset.scene_shots（GUI 显式配置优先）。
    2026-08-30 v6：stage2 双参考（封面=场景视觉主锚 + 俯视图=布局锚），
    六格只变机位、场景与封面严格一致；capability.multiview_cover_anchor 可回退。
    """
    from app.tasks.generate_scene_multiview import generate_scene_multiview as _task

    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    if asset.type != AssetType.scene:
        raise ValueError("仅场景资产支持多视角生成")
    if not asset.cover_url:
        raise ValueError("请先生成场景封面，多视角需以封面为参考")
    if shots is not None:
        # 持久化显式机位组（POV 六格渲染格式），任务读取优先级最高
        asset.scene_shots = shots
    model = _resolve_model(db, model_id, ModelType.image, "img2img")
    task = _create_asset_task(db, asset, TaskType.generate_scene_multiview, model)
    _task.delay(str(task.id))
    return asset, task


def generate_prop_image(db: Session, asset_id, model_id=None):
    """道具图生成。"""
    from app.tasks.generate_asset_cover import generate_asset_cover

    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    model = _resolve_model(db, model_id, ModelType.image, "prop")
    task = _create_asset_task(db, asset, TaskType.generate_asset_cover, model)
    generate_asset_cover.delay(str(task.id))
    return asset, task


# ─── 方案A：资产生成时记录「项目生效风格」指纹，跨项目复用做风格一致性判断 ───
def snapshot_style_fingerprint(db, project) -> dict:
    """记录资产生成那一刻的项目生效风格（style_id + 生效风格文本）。"""
    from app.services.style_service import get_effective_style_prompt

    style_prompt = ""
    if project is not None:
        try:
            style_prompt = (get_effective_style_prompt(db, project) or "").strip()
        except Exception:
            style_prompt = ""
    return {
        "style_id": str(project.style_id) if project is not None and project.style_id else None,
        "style_prompt": style_prompt or None,
    }


def asset_style_matches(db, asset, project) -> bool | None:
    """资产风格指纹与目标项目是否一致。

    - True：一致，可安全复用
    - False：不一致，不串用（应在目标项目内重新生成）
    - None：资产无指纹（迁移前旧数据/未标注），视为“未知”，放行但建议标注
    """
    fp = asset.style_fingerprint or {}
    fp_id = fp.get("style_id") or None
    fp_prompt = (fp.get("style_prompt") or "").strip() or None
    if not fp_id and not fp_prompt:
        return None
    target = snapshot_style_fingerprint(db, project)
    if fp_id and target["style_id"] and fp_id == target["style_id"]:
        return True
    if fp_prompt and target["style_prompt"] and fp_prompt == target["style_prompt"]:
        return True
    return False


def needs_ethnicity_refresh(asset) -> bool:
    """角色扩写是否缺少人种声明（旧扩写无约束 → 立绘人种不可控）。

    仅角色资产生效：扩写中未出现任何人种/肤色/发色特征词时视为"未声明人种"，
    触发重新扩写（新提示词要求按故事背景显式声明人种）。scene/prop 不参与。
    """
    if getattr(asset, "type", None) != AssetType.character:
        return False
    exp = (asset.expanded_description or "").lower()
    if not exp:
        return False  # 无扩写由调用方走正常扩写流程
    markers = (
        # 东亚/亚洲
        "east asian", "asian", "chinese", "japanese", "korean",
        # 欧美
        "caucasian", "european", "american", "western", "blonde hair",
        "blond hair", "fair skin", "blue eyes", "ginger hair", "red hair",
        # 其他地域/肤色/发色（说明扩写已声明人种特征）
        "african", "indian", "middle eastern", "arab", "latino", "hispanic",
        "mediterranean", "brown skin", "dark skin", "olive skin", "tan skin",
        "black hair", "brown eyes", "dark hair", "hazel eyes",
    )
    return not any(k in exp for k in markers)


# 服装破损/磨损词（英文）——2026-08-12 修复"人物服装撕裂"：
# 旧扩写可能含 "frayed and whitened from friction"（袖口磨白被 LLM 放大为重度磨损）
# 等词，生图模型会把磨损渲染成撕裂/破洞。扩写描述作为设定图参考时，
# 生图 prompt 须剥离一切破损类词（与技能状态词剥离同法），只保留完整服装。
_CLOTH_DAMAGE_TERMS = re.compile(
    r"\b(torn|ripped|tattered|frayed|fraying|torn fabric|ripped fabric|"
    r"torn clothes|ripped clothes|torn clothing|ripped clothing|"
    r"holes in|hole in|worn out|worn-through|damaged|damaged clothing|"
    r"shredded|scratched|scuffed|faded and torn|torn sleeves|ripped jacket|"
    r"torn pants|torn shirt|ragged|patched|darned|unraveling|unravelling|"
    r"threadbare|thinned fabric|worn at the elbows|worn cuffs|frayed cuffs)\b",
    re.IGNORECASE,
)


def strip_cloth_damage(text: str) -> str:
    """剥离角色描述中的服装破损/磨损词，只保留完整服装描述。

    仅用于角色封面/四视图等设定图 prompt；关键帧/视频的分镜级 prompt 不走此清洗
    （剧情场景中的战斗破损由分镜描述本身承载）。
    """
    s = _CLOTH_DAMAGE_TERMS.sub(" ", text)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r",\s*\.", ".", s)
    return s.strip().strip(",").strip()


def expand_description(db: Session, asset_id, model_id=None) -> str:
    """LLM 描述扩写：一句话资产描述 → 精细 prompt（同步返回）。

    按资产类型区分扩写重点：
    - character/prop：白底设定图，只描述主体本身，不含场景背景
    - scene：场景氛围图，写环境/时间/光线/空间，保留背景
    """
    from app.providers.errors import map_to_chinese

    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    brief = asset.description or asset.name
    if not brief:
        raise ValueError("资产无描述，无法扩写")
    # 2026-08-19：故事背景注入扩展——此前仅 character 注入梗概，scene/prop
    # 完全不注入 → 场景扩写自由发挥（老宅客厅被脑补成维多利亚欧式、现代主角被
    # 画成道士）。现在所有类型都注入「故事梗概 + 项目美术风格」，供时代/世界观
    # 判定；人种与服饰/建筑风格统一以此上下文锚定。
    story_hint = ""
    if asset.project_id:
        proj = db.get(Project, asset.project_id)
        if proj:
            parts_hint = []
            if proj.synopsis:
                parts_hint.append(f"故事梗概：{proj.synopsis.strip()[:600]}")
            if proj.art_style_prompt:
                parts_hint.append(f"项目美术风格：{proj.art_style_prompt.strip()[:400]}")
            story_hint = "\n".join(parts_hint)
    if asset.type == AssetType.scene:
        system = ("你是美术 prompt 工程师。把用户对场景的简短描述扩写为精细的英文生图 prompt，"
                  "包含环境布局、时间、天气、光线氛围、空间感、标志性元素，不超过 200 词，"
                  "只输出 prompt 本身。这是空场景设定图，必须保留场景环境与氛围。"
                  "重要：场景内绝对不出现任何人物/人形/人影/人群/人脸——即使原描述提到"
                  "\"族人\"\"人群\"\"人\"等，也必须删去人物成分，只描绘空无一人的环境（无人的大厅、"
                  "空荡的回廊等），因为该图将作为无人背景参考图使用。"
                  "2026-08-19 时代/世界观判定（重要）：先依据【故事背景】判断故事的时代与世界观——"
                  "现代都市 / 近现代 / 古代仙侠 / 架空古风等。场景的建筑、装饰、家具、器物风格必须"
                  "与该时代一致：a) 现代都市或近现代背景的场景，必须是中国当代/现代中式风格——"
                  "现代中式老宅、斑驳水泥或红砖墙、木格窗、旧式木沙发/八仙桌、老式电扇/座机、"
                  "瓷砖地面、老小区质感等，严禁维多利亚时代居室、欧式庄园、哥特式、英式皮沙发、"
                  "水晶吊灯、西方古典壁炉等任何西方古典风格元素；"
                  "b) 古代仙侠/古风背景才用东方古建与古典陈设（木构飞檐、砖木老宅、中式窗棂）。"
                  "判断依据以故事背景为准；若背景不可判，按场景描述中的文化线索推断。")
    elif asset.type == AssetType.prop:
        system = ("你是美术 prompt 工程师。把用户对道具的简短描述扩写为精细的英文生图 prompt，"
                  "包含道具的材质、造型、纹饰、颜色、发光/效果等细节，不超过 150 词，"
                  "只输出 prompt 本身。这是白底道具设定图，只描绘道具本身。"
                  "重要约束：1) 绝对不出现任何人/人体/人手/人脸/人影——即使原描述提到"
                  "\"人\"\"他\"\"胸口\"\"佩戴\"\"手持\"等，也必须删去人物成分；"
                  "2) 绝对不出现任何支撑物/平台/底座/台座/支架/托盘/桌面/地面/背景环境，"
                  "道具必须孤立悬浮，无阴影、无投影、无反射；"
                  "3) 只描绘道具本身特写（孤立悬浮的长枪、漂浮的符文石板等），"
                  "该图将作为道具参考图使用，背景必须纯白。"
                  "2026-08-19 时代/道具风格判定：先依据【故事背景】判断故事时代——现代都市/近现代"
                  "背景的道具必须是中国当代工业制品（现代纸张票据、智能手机、塑料/金属质感等），"
                  "严禁中世纪钱币、魔法卷轴、古风佩饰等古代元素；只有古代仙侠/古风背景才允许"
                  "玉、铜、木、符文、灵光等东方古典器物。现代与古代跨界道具（如界门、灵石）按"
                  "故事设定明确：修仙世界产的器物保留东方神秘质感，现实世界器物保持现代写实。")
    else:
        system = ("你是美术 prompt 工程师。把用户的简短描述扩写为精细的英文生图 prompt，"
                  "必须覆盖以下全部维度并给出可绘制的具体细节（不只是概括词）："
                  "1) 身材体型：身高比例/体型特征（高挑纤细、匀称结实等）、姿态气质；"
                  "2) 五官面部：脸型、眉眼、鼻梁、嘴唇、肤色肤质等可辨识特征；"
                  "3) 发型发饰：发型样式、长度、颜色、发饰/头饰；"
                  "4) 穿着：上衣/下装/外套的款式、版型、颜色、材质、纹饰；"
                  "5) 配饰：腰带/围巾/包袋/首饰/护腕等，含样式颜色；"
                  "6) 鞋帽：鞋子与帽子的款式颜色；"
                  "7) 姿态：角色必须是正面全身站立常态设定——站姿直立、两腿自然伸展、"
                  "双脚着地、正面朝向镜头，无需任何夸张姿势或动作。"
                  "姿势负面约束：即使原描述或故事背景提到蹲/坐/跪/躺/弯腰/蜷缩等"
                  "非站立动作，也一律改写成自然站立姿态，严禁把 crouching, squatting, "
                  "sitting, seated, kneeling, lying down, reclining, hunched, stooped, "
                  "bent over, crouched 等任何非站立姿态词写入扩写。"
                  "总长 200~280 词，只输出 prompt 本身。"
                  "重要：只描述角色/物品本身，绝对不要包含任何场景、背景、环境、地点、"
                  "光影氛围描述（如 standing in forest、in a room、cinematic lighting），"
                  "因为该图将作为白底设定图使用。"
                  "严禁使用设定图/概念设计/插画/动漫等风格标签术语（如 character design sheet、"
                  "character reference sheet、concept art、anime style、illustration、line art、"
                  "full-body character reference sheet、white background 等），"
                  "只客观描述角色的外貌、身材、发型、服饰、气质。"
                  "2026-08-19 时代判定（重要，先于此后的风格约束）：先依据【故事背景】判断角色所处的"
                  "时代与世界观——现代都市/近现代：角色必须是中国当代日常装束（短发或简单发型、"
                  "T恤/卫衣/衬衫/夹克/牛仔裤/运动鞋/休闲外套等现代服饰，可配手机/耳机/背包等现代配件），"
                  "严禁道袍、汉服、古装长衫、发髻、束发玉冠等任何古代元素；古代仙侠/古风/玄幻："
                  "才允许东方古典元素（见下）。若故事背景是现代+仙侠跨界（如主角在现代都市发现修仙"
                  "门），主角身份是现代人则穿现代服饰，修仙界角色才穿古装。判断依据以故事背景为主、"
                  "角色描述为辅，现代主角绝不能被误判为古代道士。"
                  "2026-08-10 风格约束：若角色属于东方武侠/仙侠/古风/玄幻设定，必须使用东方古典元素"
                  "（古风发髻或束发玉冠、东方铠甲/甲胄或汉服宽袖长袍、玉饰/护腕/绸带/符文等），"
                  "严禁西方中世纪元素：板甲/欧式骑士盔甲/西式编辫发型/欧式长剑盾牌/西式骑士披风。"
                  "若原描述含\"银甲\"\"束发\"\"武者\"等，请按东方武侠方向扩写（如东方鱼鳞甲/明光铠式甲胄、"
                  "高马尾束发或发髻玉簪、护腕束袖），保证与同项目东方仙侠角色风格统一。"
                  "2026-08-12 人种判定：依据故事背景（见【故事背景】）与角色名称/描述中的文化线索，"
                  "判断该角色应属的人种，并将人种特征明确写入扩写（发色发质、肤色、瞳色、面部特征）："
                  "中国/东亚背景的角色——黑色或深色直发、黄/暖白肤色、深色眼睛、亚洲人种面部特征；"
                  "西方/欧美背景的角色——金棕发、白皙肤色、欧美五官；中东/非洲/南亚等其他文化背景同样"
                  "按对应人种设计。人种必须与故事设定一致，严禁与背景矛盾；"
                  "若故事背景无法判断，依据角色姓名与描述的文化倾向自行推断人种并明确声明，"
                  "严禁不写人种特征，也严禁默认欧美白人金发碧眼或强行套用东亚长相。"
                  "2026-08-12 容貌美学：角色是影视剧角色，必须按影视审美塑造——主角/重要角色五官"
                  "立体精致、容貌出众（用 handsome/beautiful/striking features/refined features 等"
                  "正面美学词描绘），即使配角/群演也要端正耐看、五官协调，严禁写出 ugly/plain/"
                  "unattractive/weird 或任何贬义容貌词；五官细节描写要具体（脸型/眉眼/鼻梁/唇形/"
                  "下颌线），并确保对称协调、比例自然，给人美貌观感，严禁蜡像感/塑料感/面瘫感。"
                  "2026-08-12 服装完整约束：这是角色设定图，服装必须完整完好——"
                  "只允许极轻微的使用痕迹（如袖口泛白、布料微旧），"
                  "严禁任何撕裂/破损/破洞/磨损断裂/布料裂开/线头/开线/污渍/补丁"
                  "（严禁 torn, ripped, tattered, frayed edges, torn fabric, holes, "
                  "ripped cloth, damaged clothing, torn sleeves, ripped jacket 等词），"
                  "服装版型、面料、细节都须整洁完整可辨认。"
                  "2026-08-11 技能状态剥离：若原描述提到角色的技能/变身/神通状态"
                  "（如\"法天象地化为百丈巨人虚影\"\"化为神兽\"\"变身/觉醒/附魔发光\"等），"
                  "一律只扩写角色的常态人设（普通体型的真人外貌、日常穿着与神态），"
                  "严禁把技能状态的任何特征写入扩写——禁止半透明/虚影/幻影/巨人/百丈/发光/"
                  "法相/轮廓剪影/无头人体/幽灵化等词（semi-transparent, phantom, ethereal, "
                  "ghost, silhouette, giant, colossal, translucent, apparition, manifestation, "
                  "spirit form, astral form）。"
                  "该图是角色常态设定图，将作为参考图使用，必须是一个真实的、正常的、"
                  "完整的人物，绝不能出现任何非人形态或多余的人形轮廓。")
    model = _resolve_model(db, model_id, ModelType.text, "expand")
    provider = ProviderRegistry.for_model(model)
    # 2026-08-12：注入中文故事背景后 LLM 可能跟随输出中文 → 强制英文输出
    #（扩写将直喂英文生图模型，中文会导致结果与描述不匹配）
    system = system + " 必须用英文输出完整的生图 prompt，严禁输出中文或夹杂中文。"
    user_content = brief if not story_hint else f"{brief}\n\n【故事背景】{story_hint}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    try:
        data = provider.chat(messages)
    except Exception as e:
        raise ValueError(map_to_chinese(e))
    expanded = data["choices"][0]["message"]["content"].strip()
    asset.expanded_description = expanded
    db.commit()
    return expanded
