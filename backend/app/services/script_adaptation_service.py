"""剧本结构化改编服务：小说分析结果 → LLM → 结构化剧本 → 落库为 Project。

与 llm_script_service.generate_draft 的差异：
- 输入：小说分析结果（大纲+角色+场景+情感曲线）而非一句话 synopsis
- Prompt：更丰富，包含角色性格/成长弧、场景氛围、情感曲线
- 资产：项目级（跟项目，不跨项目复用）
- 复用：materialize_draft 落库，与现有系统无缝衔接
"""
import time

from sqlalchemy.orm import Session

from app.models.model_config import ModelType
from app.models.novel import Novel, NovelAnalysisStatus
from app.models.project import Project
from app.providers.errors import map_to_chinese
from app.services.keyframe_service import _resolve_model

ADAPTATION_PROMPT = """你是一名专业短剧编剧。基于以下小说分析结果，改编为短剧剧本。

要求返回纯 JSON（不要 markdown 代码块、不要任何解释文字），结构如下：
{{
  "title": "短剧标题，10字以内",
  "synopsis": "一句话故事梗概",
  "script": "完整剧本文本，包含全部对白与旁白",
  "assets": [
    {{
      "type": "character|scene|prop",
      "name": "资产名称（角色名/场景名/道具名，须与 segments 中引用的名称严格一致）",
      "description": "一句话描述：角色写外貌性别年龄服饰、场景写地点氛围、道具写外观"
    }}
  ],
  "episodes": [
    {{
      "title": "第N幕 标题",
      "synopsis": "本幕剧情概要",
      "narrative": "本幕幕级连贯叙事：用自然语言叙述该幕连续发生的画面/动作/运镜/光线（先…然后…最后…），不要写「第X镜」编号/时间轴表",
      "first_scene": "本幕起始画面描述（供幕首图生成）",
      "last_scene": "本幕结束画面描述（供幕尾图生成）",
      "segments": [
        {{
          "title": "本镜标题：用 4 个字概括本镜看点/行动（如「林澈拦路」「天台对峙」），禁止出现「画面描述」「镜头」等词，禁止带冒号前缀",
          "shot_type": "远景|全景|中景|近景|特写",
          "camera": "固定|推|拉|摇|移|跟",
          "shot_beats": "可选：镜头内有明确时间分节（动作/情绪/机位变化）时填本镜「分镜内多镜头运镜节拍」——每项 {{"start_sec": 秒, "end_sec": 秒, "shot_type": "景别", "camera": "运镜", "content": "该时段画面：写具体动作动词（如：猛地推门/攥拳抬头）"}}，时间连续无缝覆盖 0~duration（首拍 0 起、末拍止于 duration），拍数 2~3；无明确分节填 []；范例：[{{"start_sec": 0.0, "end_sec": 2.0, "shot_type": "近景", "camera": "推", "content": "主角推门而入"}}, {{"start_sec": 2.0, "end_sec": 4.0, "shot_type": "中景", "camera": "摇", "content": "与对方对视"}}, {{"start_sec": 4.0, "end_sec": 6.0, "shot_type": "全景", "camera": "拉", "content": "落座桌边"}}]"
          "description": "直接写画面本身（不要带任何前缀标签）：构图/光线/运镜/动作时序/环境微动态五要素完整、出现的角色/场景/道具；动作化写作——有动作的镜头用 ≥2 个具体动词（推门/攥拳/抬头/踉跄）按时序展开并给力度重量感（猛地/缓缓），运镜写「类型+幅度+速度」，禁止情绪形容词替代动作",
          "dialogue_lines": [
            {{"speaker": "说话角色名", "text": "该角色在此镜说的一句对白", "emotion": "愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊"}}
          ],
          "narration": "旁白内容，无则空字符串",
          "duration": 8.0,
          "characters": ["角色名"],
          "scene": "场景名",
          "props": ["道具名"],
          "emotion": "本镜整体氛围：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊"
        }}
      ]
    }}
  ]
}}

改编规则：
- 内心独白 → 旁白或转化为可见行为
- 时间跳跃 → 用幕切分 + 旁白过渡
- 环境描写 → 转化为画面描述
- 保留核心对话，精简次要对话
- 幕数由你按剧情自然切分（建议整部 3~12 幕），每幕 3~8 个分镜
- **duration 由你根据镜头内容在 1~{target_duration} 秒内自动配置（建议整数）**——
  镜头信息量越大、动作/情绪越重时长越长（如 8~15s），简短过场可短（如 5~6s）；
  每镜 duration 必须能读完该镜全部对白（中文真实对话约 5.5 字/秒：5s≈28字、10s≈55字、
  15s≈82字）。不要再把所有分镜写成相同时长
- 对白（dialogue_lines）每镜总字数 ≈ 该镜 duration × 5.5（5s 镜 ≤28 字，10s 镜 ≤55 字，15s 镜 ≤82 字）
- 重要：若某镜对白超过其 duration 可容纳字数，必须拆成多个连续分镜或适当调长 duration，
  拆分后镜头机位/景别可微调，保持叙事连贯；单镜 duration 不得超过 {target_duration} 秒- 旁白（narration）仅作画面/叙事描述，视频中不朗读、不计入时长
- 分镜按 MiniMax H3 视频提示词规范撰写（I2VA：首帧驱动 + 首尾帧衔接），description 必须是包含「画面五要素 + 声音事件」的完整镜头正文，禁止写成摘要句：
  * ①构图：写明前景/中景/背景空间关系与景深层次（如「前景虚化人影，对焦主角中景」），人物/道具位置明确
  * ②光线：写明光源性质与色调（如「黄昏逆光」「霓虹顶光」「夜景蓝调」），禁止空泛「氛围感」
  * ③运镜：写明机位与运动方向/速度（如「缓慢推近至特写」「低角度仰拍、轻微摇镜」），结尾落到结果/反应
  * ④动作时序：用「先…然后…」写清动作顺序与力度/重量感（如「猛地推开门、踉跄后退」），同一角色形象跨镜一致
  * ⑤环境微动态：补 1~2 个动态细节（雾气流动/灯光闪烁/水面反光/衣摆飘动/树叶轻摆等）
  * 声音事件：本镜对白/动作拟音/环境音写入 description 并给出发生的时机感，便于拼装幕级
    overall_soundscape；情绪必填 emotion
  * 幕级 narrative/first_scene/last_scene 兼顾 H3 三段式：幕内各镜画面信息 +
    幕级 overall_soundscape（环境/动作/非语言人声）+ non_diegetic_music（背景音乐描述），
    供后续视频提示词拼装
  * 相邻分镜动作/运镜连续（本镜尾帧约等于下一镜首帧），适配 I2VA 首尾帧接续生成
- narrative/first_scene/last_scene 用自然语言写幕级连贯叙事，不要出现「第X镜」编号
- assets 中所有角色/场景/道具必须在至少一个 segment 中被引用；反之 segments 中引用的名称必须在 assets 中存在
- 同名同类资产只声明一次
- dialogue_lines 中每个 speaker 必须在 characters 中；同镜多角色对白按剧情顺序排列
- 纯旁白镜（无角色对白）dialogue_lines 为空数组 []
- emotion 与 dialogue_lines[].emotion 取值必须在枚举内：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊
- 情绪须与对白内容、剧情氛围匹配（如争吵→愤怒/紧张，告别→悲伤，重逢→欢快/温馨）
- 只返回 JSON

小说分析结果：

故事大纲：
{outline}

角色清单：
{characters}

场景清单：
{scenes}

核心冲突：
{conflict}

情感曲线：
{emotion_curve}

改编要求：
- 分镜时长上限：{target_duration}s（每镜 duration 不得超过该值，由你按镜头内容在 1~上限内配置）
- 视觉风格：{style}"""


def adapt_novel(
    db: Session,
    novel_id: str,
    *,
    model_id=None,
    per_duration: int = 15,
    style: str | None = None,
    style_id=None,
    art_style_prompt: str | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    video_params: dict | None = None,
    max_retries: int = 3,
) -> Project:
    """小说分析结果 → 结构化剧本 → 落库为 Project。

    1. 读取 Novel.analysis_result
    2. LLM 生成结构化剧本 JSON（含重试）
    3. materialize_draft 落库（项目级资产 + 风格/屏幕尺寸 + 分镜时长收敛到上限）
    4. 关联 novel.project_id

    per_duration：分镜时长上限（5/10/15s），LLM 按镜头内容在 1~上限内配置每镜
    duration；台词超限自动拆分为多个连续分镜，落库时每镜 duration 收敛到上限内。

    P8：style_id / art_style_prompt / aspect_ratio 落到 Project，
    后续资产生成/关键帧/视频按所选风格与尺寸保持一致；
    三者都未配置时，LLM 改编视觉风格与资产生成均走写实兜底。
    """
    from app.providers.registry import ProviderRegistry
    from app.services.llm_script_service import _extract_json, materialize_draft, normalize_episode_title

    novel = db.get(Novel, novel_id)
    if not novel:
        raise ValueError("小说不存在")
    if novel.analysis_status != NovelAnalysisStatus.done or not novel.analysis_result:
        raise ValueError("小说尚未分析完成，请先分析")

    # 改编 prompt 的视觉风格：显式 style > 预设风格名 > 自定义文本 > 写实兜底
    if not style:
        if style_id:
            from app.models.art_style import ArtStyle
            st = db.get(ArtStyle, style_id)
            style = st.name if st else "写实摄影"
        elif art_style_prompt and art_style_prompt.strip():
            style = art_style_prompt.strip()
        else:
            style = "写实摄影"

    analysis = novel.analysis_result

    # 格式化 prompt 变量
    characters_text = "\n".join(
        f"- {c.get('name', '?')}（{c.get('role', '')}）：{c.get('personality', '')}"
        f"，外貌：{c.get('appearance', '')}，成长弧：{c.get('arc', '')}"
        for c in analysis.get("characters", [])
    ) or "（无角色信息）"

    scenes_text = "\n".join(
        f"- {s.get('name', '?')}：{s.get('description', '')}（氛围：{s.get('mood', '')}）"
        for s in analysis.get("scenes", [])
    ) or "（无场景信息）"

    conflict = analysis.get("core_conflict", {})
    conflict_text = (
        f"{conflict.get('protagonist', '?')} vs {conflict.get('antagonist', '?')}"
        f"，赌注：{conflict.get('stake', '?')}"
    ) if conflict else "（无冲突信息）"

    emotion_curve = str(analysis.get("emotion_curve", []))

    prompt = ADAPTATION_PROMPT.format(
        outline=analysis.get("outline", "（无大纲）"),
        characters=characters_text,
        scenes=scenes_text,
        conflict=conflict_text,
        emotion_curve=emotion_curve,
        target_duration=per_duration,
        style=style,
    )

    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)

    # 带重试的 LLM 调用
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = provider.chat([{"role": "user", "content": prompt}])
            content = resp["choices"][0]["message"]["content"]
            draft = _extract_json(content)

            # 落库（项目级资产，不跨项目复用）
            # P8：带上所选风格与屏幕尺寸，后续资产生成/关键帧保持一致
            # P7.6：带每幕目标时长，materialize_draft 为每幕预写 video_script
            project = materialize_draft(
                db, draft,
                synopsis=(analysis.get("outline", "") or "小说改编短剧")[:500],
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                style_id=style_id,
                art_style_prompt=art_style_prompt,
                per_duration=per_duration,
                video_params=video_params,
            )

            # 关联 novel → project（P6：记录来源小说，供章节续接追加校验）
            novel.project_id = project.id
            project.source_novel_id = novel.id
            db.commit()
            return project
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise

    raise last_err  # type: ignore[misc]



# ===== 2026-08-27 复用「分镜预览」落库：项目 = 预览 = 剧本 =====

def materialize_from_shot_plan(
    db: Session,
    novel: Novel,
    *,
    per_duration: int = 15,
    style_id=None,
    art_style_prompt: str | None = None,
    aspect_ratio: str = '16:9',
    resolution: str = '720p',
    video_params: dict | None = None,
) -> Project:
    '''直接复用「剧本分镜预览」(novel.shot_plan) 落库为项目，不重新 LLM 结构化。

    预览分镜由 structure_episode 生成（新模板：目标总时长/每镜≤10s/中近特写为主/
    对白完整），字段已是落库级，assets 随 build_script_shot_plan 一并存入预览。

    修复：此前「生成项目」把剧本原文再丢给旧流程重新结构化（旧模板每集限 4~12 镜），
    导致项目分镜只剩 7 镜、对白大面积丢失，且与确认过的 39 镜预览完全脱节。
    '''
    from app.services.llm_script_service import materialize_draft

    plan = novel.shot_plan or {}
    episodes = plan.get('episodes') or []
    if not episodes or not any((e.get('segments')) for e in episodes):
        raise ValueError('剧本暂无分镜预览，请先确认分镜预览')

    assets = []
    seen = set()
    for ep in episodes:
        for a in (ep.get('assets') or []):
            key = (a.get('type'), (a.get('name') or '').strip())
            if key[1] and key not in seen:
                seen.add(key)
                assets.append(a)

    # 2026-08-27：自动补建缺失资产——分镜引用的角色/场景/道具不在预览资产清单时，
    # 用默认描述补齐，避免生成项目时资产链断（此前一堆「未匹配 WARNING」噪音）。
    # 注意：对白 speaker 若同时出现在 characters 列表会被覆盖；纯环境说话者（广播/机器）
    # 不会凭空建角色资产。
    _segs_all = [s for ep in episodes for s in (ep.get('segments') or [])]
    _known = {(a.get('type'), (a.get('name') or '').strip()) for a in assets}
    from app.services.storyboard_parser import clean_name as _cn2, is_non_character as _nc2
    for _s in _segs_all:
        for _nm in (_s.get('characters') or []):
            _b = _cn2(_nm)
            if not _b or _nc2(_b):
                continue  # 画外音/文字等非角色名绝不建人物资产
            _k = ('character', _b)
            if _k not in _known:
                _known.add(_k)
                assets.append({'type': 'character', 'name': _b, 'description': _b + '角色'})
        for _nm in (_s.get('props') or []):
            _b = _cn2(_nm)
            if not _b:
                continue
            _k = ('prop', _b)
            if _k not in _known:
                _known.add(_k)
                assets.append({'type': 'prop', 'name': _b, 'description': _b + '道具'})
        _sc = _cn2(_s.get('scene') or '')
        if _sc and ('scene', _sc) not in _known:
            _known.add(('scene', _sc))
            assets.append({'type': 'scene', 'name': _sc, 'description': _sc + '场景'})

    out_eps = []
    for ep in episodes:
        segs = []
        for s in ep.get('segments') or []:
            try:
                d = float(s.get('duration') or 5.0)
            except (TypeError, ValueError):
                d = 5.0
            seg = dict(s)
            seg['duration'] = min(max(d, 4.0), 10.0)
            segs.append(seg)
        out_eps.append({
            'title': ep.get('title') or ('第' + str(len(out_eps) + 1) + '幕'),
            'synopsis': (ep.get('synopsis') or '').strip(),
            'segments': segs,
        })

    first_syn = (out_eps[0].get('synopsis') or '').strip()[:500]
    draft = {
        'title': novel.title,
        'synopsis': first_syn or (novel.title or ''),
        'script': novel.raw_text or '',
        'assets': assets,
        'episodes': out_eps,
    }
    project = materialize_draft(
        db, draft,
        synopsis=first_syn or (novel.title or ''),
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        style_id=style_id,
        art_style_prompt=art_style_prompt,
        per_duration=per_duration,
        video_params=video_params,
    )
    project.title = (novel.title or '未命名短剧')[:200]
    project.source_novel_id = novel.id
    if style_id:
        project.style_id = style_id
    if art_style_prompt and str(art_style_prompt).strip():
        project.art_style_prompt = str(art_style_prompt).strip()
    if aspect_ratio in ('16:9', '9:16', '1:1', '4:3', '3:4'):
        project.aspect_ratio = aspect_ratio
    if resolution in ('480p', '720p', '768p'):
        project.resolution = resolution
    if video_params:
        project.video_params = {k: v for k, v in video_params.items() if v not in (None, '')}
    novel.project_id = project.id
    db.commit()
    return project


# ===== 2026-08-22 剧本直读改编（无分析）：剧本文本 → 项目 =====

def adapt_script_direct(
    db: Session,
    novel_id: str,
    *,
    model_id=None,
    per_duration: int = 15,
    style: str | None = None,
    style_id=None,
    art_style_prompt: str | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    video_params: dict | None = None,
) -> Project:
    """剧本直读生成项目：跳过「小说分析」步骤，直接把剧本原文改编为 Project。

    剧本本身就是结构化产物，无需 Map-Reduce 分析。流程：
    1. split_chapters 按【第X集】标记拆分剧本（无标记按字数均分）
    2. 逐集 structure_episode → 场次级结构草案（资产 + 分镜）
    3. apply_episode 逐幕落库为项目（同名资产自动复用，幕 = 集）
    4. 关联 novel.project_id / project.source_novel_id（供后续章节续接校验）
    """
    from app.services import script_writing_service
    from app.services.llm_script_service import normalize_episode_title
    from app.services.novel_analysis_service import split_chapters

    novel = db.get(Novel, novel_id)
    if not novel:
        raise ValueError("剧本不存在")
    raw = (novel.raw_text or '').strip()
    if not raw:
        raise ValueError('剧本内容为空，无法生成项目')

    # 2026-08-27：分镜预览已确认（novel.shot_plan 有段）→ 直接复用预览落库，
    # 不再重新 LLM 结构化，保证项目分镜/对白 = 预览 = 剧本，一字不漏
    _plan = novel.shot_plan or {}
    if _plan.get('episodes') and any(e.get('segments') for e in _plan['episodes']):
        return materialize_from_shot_plan(
            db, novel,
            per_duration=per_duration, style_id=style_id,
            art_style_prompt=art_style_prompt, aspect_ratio=aspect_ratio,
            resolution=resolution, video_params=video_params,
        )

    # 2026-08-27 直落优先（与剧本库分镜预览一致）：确认稿（分镜N-M（X秒）齐全）直接
    # 按「场景→镜头」落库，分镜 duration 严格取稿子秒数、镜头不合并；非确认稿格式
    # 才走 LLM 逐集结构化回退。资产由分镜引用（场景名/对白说话人）自动建立基础清单。
    from app.services.storyboard_parser import parse_storyboard

    direct_eps = parse_storyboard(raw)
    if direct_eps:
        from app.services.llm_script_service import materialize_draft

        assets: list[dict] = []
        seen_scene: set[str] = set()
        seen_char: set[str] = set()
        for ep in direct_eps:
            if ep.get("title") and ep["title"] not in seen_scene:
                seen_scene.add(ep["title"])
                assets.append({"type": "scene", "name": ep["title"], "description": f"{ep['title']}场景"})
            for seg in ep.get("segments") or []:
                for name in seg.get("characters") or []:
                    if name and name not in seen_char:
                        seen_char.add(name)
                        assets.append({"type": "character", "name": name, "description": f"{name}角色"})
        draft = {
            "title": novel.title,
            "synopsis": (novel.outline or {}).get("synopsis") or (raw[:200]),
            "script": raw,
            "assets": assets,
            "episodes": [
                {"title": ep.get("title") or f"第{i+1}幕", "synopsis": "", "segments": ep.get("segments") or []}
                for i, ep in enumerate(direct_eps)
            ],
        }
        project = materialize_draft(
            db, draft,
            synopsis=(novel.outline or {}).get("synopsis") or novel.title,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            style_id=style_id,
            art_style_prompt=art_style_prompt,
            per_duration=per_duration,
            video_params=video_params,
        )
        novel.project_id = project.id
        project.source_novel_id = novel.id
        project.title = (novel.title or "未命名短剧")[:200]
        db.commit()
        return project

    # 2026-08-27：无预览时先按新模板生成分镜预览再复用（与确认式流程一致）。
    # 避免退回旧模板逐集结构化（旧模板每集限 4~12 镜、丢对白）。失败才回退旧链路兜底。
    try:
        _plan2 = build_script_shot_plan(
            db, str(novel.id), model_id=model_id, per_duration=per_duration,
            target_total_seconds=300,
        )
        if _plan2.get('episodes') and any(e.get('segments') for e in _plan2['episodes']):
            return materialize_from_shot_plan(
                db, novel,
                per_duration=per_duration, style_id=style_id,
                art_style_prompt=art_style_prompt, aspect_ratio=aspect_ratio,
                resolution=resolution, video_params=video_params,
            )
    except Exception as _prev_err:  # noqa: BLE001
        import logging as _lg
        _lg.getLogger(__name__).warning("按新模板生成预览失败，回退旧链路（可能丢内容）: %s", _prev_err)

    chapters = split_chapters(raw)
    if not chapters:
        raise ValueError('剧本内容为空，无法生成项目')

    project: Project | None = None
    for idx, ch in enumerate(chapters, start=1):
        # 幕标题从源头清掉「第N集」标记（如「第1集 夜店救人」→「夜店救人」），
        # 避免结构化为「第1幕 第1集 夜店救人」之类带集字样的幕名。
        ep_title = normalize_episode_title((ch.get("title") or "").strip("【】").strip() or f"第{idx}集", idx)
        ep_draft = script_writing_service.structure_episode(
            db, ch["text"], idx, ep_title, model_id
        )
        project = script_writing_service.apply_episode(
            db, project, ep_draft, ch["text"], idx,
            per_duration=per_duration, project_title=novel.title,
        )
    if project is None:
        raise ValueError("剧本改编失败：未生成任何幕")

    # 项目级配置落地（风格/尺寸/分辨率）并与剧本关联
    try:
        project.title = (novel.title or "未命名短剧")[:200]
        project.source_novel_id = novel.id
        if style_id:
            project.style_id = style_id
        if art_style_prompt and str(art_style_prompt).strip():
            project.art_style_prompt = str(art_style_prompt).strip()
        if aspect_ratio in ("16:9", "9:16", "1:1", "4:3", "3:4"):
            project.aspect_ratio = aspect_ratio
        if resolution in ("480p", "720p", "768p"):
            project.resolution = resolution
        if video_params:
            project.video_params = {k: v for k, v in video_params.items() if v not in (None, "")}
        novel.project_id = project.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    return project


# ===== P6 章节续接追加 =====

CONTINUATION_PROMPT = """你是一名专业短剧编剧。基于以下小说章节内容与已有项目上下文，把**新增章节**改编为短剧剧本的**新幕**，续接到已有项目。

要求返回纯 JSON（不要 markdown 代码块、不要任何解释文字），结构如下：
{{
  "title": "本批章节的短剧标题（沿用已有项目主题，10字以内）",
  "synopsis": "本批章节的故事梗概",
  "assets": [
    {{
      "type": "character|scene|prop",
      "name": "资产名称",
      "description": "一句话描述"
    }}
  ],
  "episodes": [
    {{
      "title": "第N幕 标题",
      "synopsis": "本幕剧情概要",
      "segments": [
        {{
          "title": "本镜标题：用 4 个字概括本镜看点/行动（如「林澈拦路」「天台对峙」），禁止出现「画面描述」「镜头」等词，禁止带冒号前缀",
          "shot_type": "远景|全景|中景|近景|特写",
          "camera": "固定|推|拉|摇|移|跟",
          "shot_beats": "可选：镜头内有明确时间分节（动作/情绪/机位变化）时填本镜「分镜内多镜头运镜节拍」——每项 {{"start_sec": 秒, "end_sec": 秒, "shot_type": "景别", "camera": "运镜", "content": "该时段画面：写具体动作动词（如：猛地推门/攥拳抬头）"}}，时间连续无缝覆盖 0~duration（首拍 0 起、末拍止于 duration），拍数 2~3；无明确分节填 []；范例：[{{"start_sec": 0.0, "end_sec": 2.0, "shot_type": "近景", "camera": "推", "content": "主角推门而入"}}, {{"start_sec": 2.0, "end_sec": 4.0, "shot_type": "中景", "camera": "摇", "content": "与对方对视"}}, {{"start_sec": 4.0, "end_sec": 6.0, "shot_type": "全景", "camera": "拉", "content": "落座桌边"}}]"
          "description": "直接写画面本身（不要带任何前缀标签）：构图/光线/运镜/动作时序/环境微动态五要素完整、出现的角色/场景/道具；动作化写作——有动作的镜头用 ≥2 个具体动词（推门/攥拳/抬头/踉跄）按时序展开并给力度重量感（猛地/缓缓），运镜写「类型+幅度+速度」，禁止情绪形容词替代动作",
          "dialogue_lines": [
            {{"speaker": "说话角色名", "text": "该角色在此镜说的一句对白", "emotion": "愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊"}}
          ],
          "narration": "旁白内容，无则空字符串",
          "duration": 8.0,
          "characters": ["角色名"],
          "scene": "场景名",
          "props": ["道具名"],
          "emotion": "本镜整体氛围"
        }}
      ]
    }}
  ]
}}

**续接规则（必须遵守）**：
1. 只改编"本批章节"的内容，不得重写/回顾前文剧情（可用旁白简短过渡）
2. 角色/场景/道具与**已有资产清单**同名的：assets 中必须沿用同名（description 可补充但不改名），禁止新增重名资产
3. 只有本批新出现的角色/场景/道具才在 assets 中新增
4. 剧情须与**前文概要**衔接，角色性格/关系不得与前文矛盾
5. segments 中引用的角色/场景/道具名必须在 assets（含已有）中存在
6. emotion 取值：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊
7. 每幕 1~3 分钟，总片长与新增章节规模匹配；每个场景 3~8 个分镜，每镜 3~8 秒
8. duration 为 2~8 之间的数字
9. 只返回 JSON
10. 分镜 description 按「画面五要素 + 声音事件」撰写完整镜头正文（禁止摘要句）：
    构图（前后景/景深）、光线（光源/色调）、运镜（机位+运动方向速度）、动作时序
    （先…然后…+力度/重量感）、环境微动态（雾/灯光/水波/衣摆/树叶）+ 声音时机感，
    相邻分镜运镜/动作连续（尾帧≈下一镜首帧），可直接作 H3 integrated_multimodal_description 的 [Shot N] 块原料

已有项目上下文：

已有幕数：{episode_count}
已有幕清单：
{existing_episodes}

已有资产清单：
{existing_assets}

前文概要（前序章节摘要，供衔接参考）：
{prior_summary}

本批章节内容：
{chapters_text}"""


def adapt_novel_continuation(
    db: Session,
    novel_id: str,
    project_id: str,
    *,
    chapter_start: int,
    chapter_end: int,
    model_id=None,
    max_retries: int = 3,
) -> dict:
    """按章节范围改编并续接到已有项目（P6）。

    1. 校验：novel 已分析、project 存在、断点顺序（chapter_start 必须等于
       project.processed_upto_chapter + 1，防乱序/重复追加）
    2. split_chapters 取 [chapter_start, chapter_end] 子集 → 逐章摘要
    3. 收集前文上下文（已有幕/资产清单 + 前序章节摘要）
    4. LLM 生成新幕剧本 → materialize_draft(existing_project=project) 续接落库
       （同名资产自动复用，幕 index 从已有最大幕号 +1 起）
    5. 更新 project.processed_upto_chapter = chapter_end

    返回 {"project": Project, "added_episodes": list[Episode], "chapters": list[dict]}
    """
    from app.providers.registry import ProviderRegistry
    from app.services.llm_script_service import _extract_json, materialize_draft
    from app.services.novel_analysis_service import split_chapters, summarize_chapter

    novel = db.get(Novel, novel_id)
    if not novel:
        raise ValueError("小说不存在")
    if novel.analysis_status != NovelAnalysisStatus.done or not novel.analysis_result:
        raise ValueError("小说尚未分析完成，请先分析")

    project = db.get(Project, project_id)
    if not project:
        raise ValueError("目标项目不存在")

    # 断点校验：章节必须顺序续接（processed_upto_chapter 为空视为 0）
    upto = project.processed_upto_chapter or 0
    if chapter_start != upto + 1:
        raise ValueError(
            f"章节追加必须按序续接：已改编到第 {upto} 章，本批应从第 {upto + 1} 章开始"
            f"（收到 chapter_start={chapter_start}）"
        )
    if chapter_end < chapter_start:
        raise ValueError("chapter_end 必须 >= chapter_start")

    chapters = split_chapters(novel.raw_text)
    if chapter_end > len(chapters):
        raise ValueError(f"小说共 {len(chapters)} 章，chapter_end={chapter_end} 超出范围")
    batch = chapters[chapter_start - 1 : chapter_end]

    # 逐章摘要（本批章节，串行 LLM）
    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    summaries = []
    for ch in batch:
        try:
            summaries.append(summarize_chapter(ch, provider))
        except Exception as e:
            summaries.append(
                {"index": ch["index"], "title": ch["title"], "summary": f"(摘要失败: {map_to_chinese(e)})"}
            )

    chapters_text = "\n\n".join(
        f"第{s.get('index')}章 {s.get('title', '')}：{s.get('summary', '')}"
        for s in summaries
    )

    # 前文概要：analysis_result 中前序章节的摘要（无则用大纲）
    analysis = novel.analysis_result
    prior = []
    for s in analysis.get("chapters_summary") or []:
        if (s.get("index") or 0) < chapter_start:
            prior.append(f"第{s.get('index')}章 {s.get('title', '')}：{s.get('summary', '')}")
    prior_summary = "\n".join(prior) or analysis.get("outline", "（无前文信息）")

    # 已有幕/资产清单
    eps = sorted(project.episodes, key=lambda e: e.index)
    existing_episodes = "\n".join(
        f"- 幕{e.index + 1} {e.title}：{e.synopsis or ''}" for e in eps
    ) or "（无）"
    existing_assets = "\n".join(
        f"- [{a.type.value}] {a.name}：{a.description or ''}" for a in project.assets
    ) or "（无）"

    prompt = CONTINUATION_PROMPT.format(
        episode_count=len(eps),
        existing_episodes=existing_episodes,
        existing_assets=existing_assets,
        prior_summary=prior_summary[:4000],
        chapters_text=chapters_text[:8000],
    )

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = provider.chat([{"role": "user", "content": prompt}])
            content = resp["choices"][0]["message"]["content"]
            draft = _extract_json(content)

            # 续接落库：同名资产复用，幕号从已有最大 +1 续接
            project = materialize_draft(
                db, draft,
                synopsis=(draft.get("synopsis") or novel.title)[:500],
                existing_project=project,
            )
            # 本次新增的幕 = index >= 续接前幕数（幕 index 从已有最大 +1 起）
            added = [e for e in project.episodes if e.index >= len(eps)]
            project.processed_upto_chapter = chapter_end
            db.commit()
            return {"project": project, "added_episodes": added, "chapters": batch}
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise

    raise last_err  # type: ignore[misc]


def build_script_shot_plan(
    db, novel_id, *, model_id=None, per_duration: int = 15,
    task_id: str | None = None, target_total_seconds: int = 300,
) -> dict:
    """确认式生成项目流程：写剧本后预生成「分镜预览」并存入 novel.shot_plan（不建项目）。

    拆幕 → 逐幕结构化为分镜（按 MiniMax H3 分镜规范，structure_episode）。
    task_id 非空时同步回报逐幕进度（供智能体前端展示「分镜」阶段实时进度）。
    用户在剧本库确认并指定 视频大小/清晰度/风格 等参数后，再经 adapt_script_direct 改编为项目。
    """
    from app.tasks.base import now, update_task

    from app.services import script_writing_service
    from app.services.llm_script_service import normalize_episode_title
    from app.services.novel_analysis_service import split_chapters

    try:
        if task_id:
            update_task(db, task_id, status="running", started_at=now(), progress=5)
        novel = db.get(Novel, novel_id)
        if novel is None:
            raise ValueError("剧本不存在")
        raw = (novel.raw_text or "").strip()
        if not raw:
            raise ValueError("剧本内容为空，无法生成分镜预览")

        # 2026-08-27 直落优先：确认稿（分镜N-M（X秒）、景别/画面/AI提示词齐全）直接原样
        # 落为 segments——时长取稿子逐镜秒数、镜头逐镜独立不合并；解析不出（非确认稿
        # 格式）再走 LLM 逐集结构化回退。
        from app.services.storyboard_parser import parse_storyboard

        direct_episodes = parse_storyboard(raw)
        if direct_episodes:
            # 2026-08-30：source=direct 标记「按导入剧本已写好的分镜直落」（前端徽标展示）
            plan = {
                "episodes": direct_episodes,
                "episode_count": len(direct_episodes),
                "segment_count": sum(len(e["segments"]) for e in direct_episodes),
                "source": "direct",
            }
            novel.shot_plan = plan
            db.commit()
            if task_id:
                update_task(db, task_id, status="succeeded", progress=100, finished_at=now())
            return plan

        chapters = split_chapters(raw)
        total = max(1, len(chapters))
        episodes = []
        for idx, ch in enumerate(chapters, start=1):
            _body_txt = (ch.get("text") or "").strip()
            # 2026-08-27：纯「集头」章节（如【第1集 标题】独占一行无正文）跳过，
            # 避免哑巴章节被结构化失败拖垮整个分镜预览（智能体直写完整剧本时常见）。
            if len(_body_txt) < 50 and _body_txt.startswith("【"):
                continue
            ep_title = normalize_episode_title(
                (ch.get("title") or "").strip("【】").strip() or f"第{idx}集", idx,
            )
            ep = script_writing_service.structure_episode(
                db, _body_txt, idx, ep_title, model_id,
                target_total_seconds=target_total_seconds,
            )
            segments = ep.get('segments') or []
            # 2026-08-27：预览同时落资产清单——建项目时直接复用预览，资产一并带过去
            episodes.append({
                'index': idx - 1,
                'title': ep.get('title') or ep_title,
                'synopsis': (ep.get('synopsis') or '').strip(),
                'segments': segments,
                'assets': ep.get('assets') or [],
            })
            if task_id:
                update_task(db, task_id, progress=min(95, 10 + int(85 * idx / total)))
        plan = {
            "episodes": episodes,
            "episode_count": len(episodes),
            "segment_count": sum(len(e["segments"]) for e in episodes),
            # 2026-08-30：source=llm 标记「AI 生成的分镜预览」（前端徽标展示）
            "source": "llm",
        }
        novel.shot_plan = plan
        db.commit()
        if task_id:
            update_task(db, task_id, status="succeeded", progress=100, finished_at=now())
        return plan
    except Exception:
        db.rollback()
        if task_id:
            try:
                from app.models.task import TaskStatus
                from app.tasks.base import update_task as _ut
                _ut(db, task_id, status=TaskStatus.failed, finished_at=now())
            except Exception:
                pass
        raise
