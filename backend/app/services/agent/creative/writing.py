"""创作对接层 · 后台写作任务：写小说 / 分集剧本（提交 Celery 任务）。

从 agent_service.py 剥离（原行号 3972~4105 区域），逻辑未改动。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session


def _tool_write_novel(db: Session, args: dict, model=None) -> tuple:
    """创建/续写长篇小说：创建 Novel（或复用已有）+ 提交 Celery 逐章写作任务。

    参数：title（新建必填）、brief（题材设定）、genre（类型，可选）、
          chapters（章数，默认 10）、novel_id（可选，续写：复用大纲从断点继续）。
    返回 (novel_id, task_id, title)。
    """
    from app.models.novel import Novel
    from app.models.task import Task, TaskStatus, TaskType
    from app.tasks.generate_novel import write_novel_task

    novel_id = args.get("novel_id")
    title = (args.get("title") or "").strip()
    brief = (args.get("brief") or "").strip()
    genre = (args.get("genre") or "").strip()
    try:
        chapters = int(args.get("chapters") or 10)
    except (TypeError, ValueError):
        chapters = 10
    chapters = max(1, min(100, chapters))

    if novel_id:
        novel = db.get(Novel, novel_id)
        if not novel:
            raise ValueError("小说不存在")
        if novel.writing_status == "done":
            raise ValueError(f"《{novel.title}》已写完；如需续写新章节，请重新规划一部新小说")
        title = novel.title
    else:
        if not title:
            raise ValueError("小说标题不能为空")
        novel = Novel(title=title[:200], raw_text="", writing_status="none")
        db.add(novel)
        db.commit()
        db.refresh(novel)

    task = Task(
        type=TaskType.write_novel,
        target_type="novel",
        target_id=novel.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    write_novel_task.delay(
        str(task.id), str(novel.id), brief, genre, chapters,
        str(model.id) if model else None,
    )
    return novel.id, task.id, title


def _tool_write_script(db: Session, args: dict, model=None) -> tuple:
    """创建/续写分集剧本（剧本库）：创建 Novel（剧本）+ 提交后台逐集剧本写作任务。

    参数：title（剧本标题，必填）、brief（题材设定，必填）、episodes（集数，默认 10，
    1~100）、genre（类型，可选）、novel_id（可选，续写：复用大纲从断点继续）。
    返回 (novel_id, task_id, title)。剧本写入剧本库，用户可在「剧本库 → 分析 → 生成项目」。
    """
    from app.models.novel import Novel
    from app.models.task import Task, TaskStatus, TaskType
    from app.tasks.generate_script import write_script_lib_task

    title = (args.get("title") or "").strip()
    brief = (args.get("brief") or "").strip()
    genre = (args.get("genre") or "").strip()
    style_mode = (args.get("style_mode") or "").strip() or None
    full_script = (args.get("full_script") or "").strip()
    if not title:
        raise ValueError("剧本标题不能为空")
    # 2026-08-27：有已确认的完整剧本/分镜正文（对话里细化过）则直写落库，
    # 此时 brief 可为空（自动从正文截取归档），无 full_script 时仍必须有 brief。
    if not brief:
        if full_script:
            brief = (full_script[:120].replace("\n", " ") or "都市情感短剧").strip()
        else:
            raise ValueError("题材设定不能为空")
    try:
        episodes = int(args.get("episodes") or 10)
    except (TypeError, ValueError):
        episodes = 10
    episodes = max(1, min(100, episodes))

    novel_id = (args.get("novel_id") or "").strip() or None
    target_id: object
    if novel_id:
        from uuid import UUID as _UUID
        try:
            novel_uuid = _UUID(novel_id)
        except Exception:
            novel_uuid = None  # noqa: B904
        novel = db.get(Novel, novel_uuid) if novel_uuid else None
        if novel is None:
            # 指定的剧本不存在/已被删除 → 回退同题复用或新建
            novel_id = None
        else:
            if novel.writing_status == "done":
                raise ValueError(f"《{novel.title}》已写完；如需续写新集，请重新规划一部新剧本")
            title = novel.title
            target_id = novel.id
    if not novel_id:
        # 去重：同题剧本存在且未写完 → 复用（续写），避免同名剧本并存
        dup = db.scalars(
            select(Novel).where(Novel.title == title)
            .order_by(Novel.created_at.desc(), Novel.id.desc())
        ).first()
        if dup is not None and dup.writing_status != "done":
            novel = dup
            target_id = dup.id
        else:
            novel = Novel(title=title[:200], raw_text="", writing_status="none")
            db.add(novel)
            db.commit()
            db.refresh(novel)
            target_id = novel.id

    # 2026-08-28（四阶段确认流）：plan_script_outline 已确认的大纲随调用一并落库，
    # 后台 write_script_lib_task 会复用 novel.outline 直接写作，不重新规划；
    # 未传 outline（如 gen.py 直连 / 续写）时行为不变，由后台任务自行规划/复用。
    outline_arg = args.get("outline")
    if isinstance(outline_arg, dict) and (outline_arg.get("episodes") or []):
        novel.outline = outline_arg
        db.commit()

    task = Task(
        type=TaskType.write_script,
        target_type="novel",
        target_id=target_id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    if full_script:
        # 2026-08-27：完整剧本/分镜正文直写落库——对话里已确认过的内容 1:1 保留，
        # 由 write_script_lib_task 识别 full_script 标记后跳过重新生成、直接收尾
        #（拆幕分镜预览 + 海报）。raw_text 保留原文与【第X场】标题，供按场拆幕。
        import re as _re
        from app.services.novel_analysis_service import split_chapters as _split_chapters

        base = full_script
        if not _re.search(r"^【第[x\d]+集", base, _re.MULTILINE):
            base = f"【第1集 {novel.title}】\n\n{base}"
        novel.raw_text = base
        if not novel.outline:
            novel.outline = {
                "title": novel.title, "synopsis": brief,
                "episodes": [{"title": novel.title, "brief": brief}],
            }
        # 不在此置 done：由 write_script_lib_task 收尾时统一置（其前置
        # writing_status==done 守卫会把直写路径拦掉）。
        novel.chapters_count = len(_split_chapters(base))
        novel.word_count = len(base)
        db.commit()
        task.provider_task_id = "full_script"
        db.commit()

    write_script_lib_task.delay(
        str(task.id), str(novel.id), brief, episodes, genre or None,
        str(model.id) if model else None, style_mode, full_script or None,
    )
    return novel.id, task.id, title


def _tool_plan_script_outline(db: Session, session_id, args: dict, model=None) -> dict:
    """【四阶段确认流·第①步】规划分集集纲（同步，不创建剧本文档、不提交写作任务）。

    仅调用 LLM 生成集纲并产出预览，供展示给用户确认。大纲作为「当前确认版单一事实来源」
    写入会话创作状态卡（role_key=script_outline）；只有用户在对话中确认大纲后，
    write_script 才会真正提交后台写作（executor 侧以状态卡 + 用户确认消息双重校验）。
    """
    from app.services import script_writing_service
    from app.services.agent.creative.subagent import _upsert_creative_state

    title = (args.get("title") or "").strip()
    brief = (args.get("brief") or "").strip()
    genre = (args.get("genre") or "").strip()
    style_mode = (args.get("style_mode") or "").strip() or None
    if not title:
        raise ValueError("剧本标题不能为空")
    if not brief:
        raise ValueError("题材设定不能为空")
    try:
        episodes = int(args.get("episodes") or 10)
    except (TypeError, ValueError):
        episodes = 10
    episodes = max(1, min(100, episodes))

    import json as _json
    from app.models.novel import Novel as _Novel
    # 续写：带 novel_id 时延长既有集纲（严格延续前情），而非另起炉灶
    prior = None
    novel_id = (args.get("novel_id") or "").strip() or None
    if novel_id:
        from uuid import UUID as _U
        try:
            _n = db.get(_Novel, _U(novel_id))
        except Exception:  # noqa: BLE001
            _n = None
        if _n is not None and isinstance(_n.outline, dict) and (_n.outline.get("episodes") or []):
            prior = _n.outline
    outline = script_writing_service.plan_episode_outline(
        db, brief, episodes, title, genre or None,
        model_id=str(model.id) if model else None,
        prior=prior, style_mode=style_mode,
    )
    # 大纲落创作状态卡：write_script 门槛以它为准（模型转述的大纲参数不可信）
    _upsert_creative_state(
        db, session_id, "script_outline",
        _json.dumps(outline, ensure_ascii=False),
    )
    db.commit()

    eps = outline.get("episodes") or []
    lines = [f"《{outline.get('title') or title}》集纲预览（{len(eps)} 集）："]
    if outline.get("synopsis"):
        lines.append(f"- 故事梗概：{outline['synopsis']}")
    if outline.get("world"):
        lines.append(f"- 世界观：{outline['world']}")
    for ep in eps:
        lines.append(f"- 第{ep.get('index') or '?'}集《{ep.get('title') or ''}》：{(ep.get('brief') or '')[:120]}")
    return {
        "ok": True,
        "params": args,
        "message": (
            "\n".join(lines)
            + "\n\n请把这份集纲原样展示给用户，等待用户确认（如「没问题/确认/可以/生成完整剧本」）；"
            "用户确认或发出延续指令后，再调用 write_script（title/brief/episodes 与此处一致，并传 confirm_outline=true）。"
        ),
    }


def _script_outline_confirmed(db: Session, session_id) -> bool:
    """四阶段确认流·大纲确认门槛：会话里存在「已成功的 plan_script_outline 调用」，
    且其后出现过命中确认词的 user 消息（用户真实确认过，而非模型自说自话连跑）。

    返回 False 时 write_script 拒绝执行——从机制上杜绝「一轮请求连跑 写剧本/分镜/封面」。
    """
    from app.models.agent import AgentMessage

    plan = db.scalars(
        select(AgentMessage).where(
            AgentMessage.session_id == session_id,
            AgentMessage.role == "tool",
            AgentMessage.tool_name == "plan_script_outline",
            AgentMessage.tool_status == "succeeded",
        ).order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(1)
    ).first()
    if plan is None:
        return False
    # 该大纲调用必须是「本轮对话之前/之中」已经呈现给用户的结果——若当前轮次还没任何
    # 用户消息排在它之后，说明模型在同一个用户请求里连跑，视为未确认。
    # 2026-08-28 放宽：除显式确认词外，用户看完大纲后发的「延续推进」指令（生成完整剧本 /
    # 开始写 / 就按这个写 / 继续 等）同样视为对大纲的确认——用户语义明确，不应再拦一道。
    confirm_terms = (
        "确认", "没问题", "可以", "就按这个", "按这个来", "按这个写", "开写", "开写吧",
        "写吧", "开始写", "同意", "同意这个", "OK", "ok", "好的", "行，", "行吧",
        "生成完整剧本", "生成完整", "完整剧本", "生成剧本", "生成剧本吧", "直接写", "直接生成",
        "写剧本", "写完整剧本", "接着写", "继续写", "下一步", "生成吧", "开始生成",
    )
    users = db.scalars(
        select(AgentMessage).where(
            AgentMessage.session_id == session_id,
            AgentMessage.role == "user",
            AgentMessage.content.isnot(None),
        ).order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
    ).all()
    for u in users:
        if u.created_at > plan.created_at:
            content = (u.content or "").strip()
            if content and any(t in content for t in confirm_terms):
                return True
    return False


def _pick_novel(db: Session, args: dict):
    """确定目标剧本：显式 novel_id > 最近一本有正文的剧本。"""
    from app.models.novel import Novel

    nid = (args.get("novel_id") or "").strip()
    if nid:
        from uuid import UUID as _U
        try:
            n = db.get(Novel, _U(nid))
        except Exception:
            n = None  # noqa: BLE001
        if n is not None:
            return n
    return db.scalars(
        select(Novel)
        .where(Novel.raw_text.isnot(None), Novel.raw_text != "")
        .order_by(Novel.created_at.desc(), Novel.id.desc())
    ).first()


def _plan_from_direct(novel, direct_eps):
    """把 parse_storyboard 解析出的确认稿组装成 shot_plan（含资产清单）。"""
    episodes = []
    global_assets = []
    seen = set()

    def add(kind, name):
        from app.services.storyboard_parser import clean_name, is_non_character
        nm = clean_name(name)
        if not nm:
            return
        if kind == 'character' and is_non_character(nm):
            return  # 画外音/文字等非角色名绝不建人物资产
        key = (kind, nm)
        if key not in seen:
            seen.add(key)
            label = '角色' if kind == 'character' else ('场景' if kind == 'scene' else '道具')
            global_assets.append({'type': kind, 'name': key[1], 'description': key[1] + label})

    for i, ep in enumerate(direct_eps):
        segs = []
        for s in (ep.get('segments') or []):
            try:
                d = float(s.get('duration') or 8.0)
            except (TypeError, ValueError):
                d = 8.0
            seg = dict(s)
            seg['duration'] = min(max(d, 4.0), 10.0)
            segs.append(seg)
            for nm in (seg.get('characters') or []):
                add('character', nm)
            for nm in (seg.get('props') or []):
                add('prop', nm)
            sc = str(seg.get('scene') or '').strip()
            if sc:
                add('scene', sc)
        episodes.append({
            'index': i,
            'title': ep.get('title') or ('第' + str(i + 1) + '幕'),
            'synopsis': (ep.get('synopsis') or '').strip(),
            'segments': segs,
            'assets': global_assets,
        })
    return {
        'episodes': episodes,
        'episode_count': len(episodes),
        'segment_count': sum(len(e['segments']) for e in episodes),
        # 2026-08-30：source=direct 标记「复用对话里写好的分镜直落」（前端徽标展示）
        'source': 'direct',
    }


def _tool_generate_shot_preview(db: Session, args: dict, model=None) -> str:
    """【三阶段②】生成分镜预览：优先复用聊天里写好的分镜，否则 LLM 生成。

    优先：智能体在对话中已按「分镜N-标题（X秒）」规范格式写好的分镜——解析直落入库，
    不重新生成（快且与对话内容完全一致）；正文里没有该格式分镜时，才由系统按剧本
    LLM 生成（镜头数对齐总时长/每镜≤10s/中近特写为主）。结果写入 novel.shot_plan。
    """
    from app.services.storyboard_parser import parse_storyboard

    novel = _pick_novel(db, args)
    if novel is None:
        raise ValueError("未找到目标剧本，请先调用 write_script 写剧本")
    # 2026-08-28（四阶段确认流·第③步门槛）：剧本必须已写完（后台逐集写作完成）
    if novel.writing_status != "done" or not (novel.raw_text or "").strip():
        raise ValueError(
            "剧本尚未写完：后台逐集写作仍在进行中。请等待剧本写作完成、并先就成稿与用户确认大纲/剧本后，"
            "再调用本工具生成分镜预览。"
        )

    # ① 复用：正文里的确认稿分镜 → 直落解析，不走 LLM
    direct = parse_storyboard(novel.raw_text or '')
    if direct and any(e.get('segments') for e in direct):
        # 2026-08-27：五要素补缺（电影感标准：构图/光线/运镜/动作/质感/景深/气氛）——
        # 单次批量小调用只补缺失项，不改写原文、不整镜重写。
        # 对白不补（纯画面镜无对白属正常）。
        from app.services.storyboard_enrich import enrich_direct_episodes
        filled = enrich_direct_episodes(db, direct)
        plan = _plan_from_direct(novel, direct)
        novel.shot_plan = plan
        db.commit()
        segs = [s for e in direct for s in (e.get('segments') or [])]
        tot = round(sum(float(s.get('duration') or 0) for s in segs))
        fill_tip = (f'，并按电影感标准补齐 {filled} 镜缺失要素' if filled else '')
        return (
            f'《{novel.title}》分镜预览已生成：{plan["episode_count"]} 幕 / {plan["segment_count"]} 镜 / '
            f'约 {tot} 秒（复用你在对话中写好的分镜，直落解析，未重新生成{fill_tip}）。'
            '请用户确认分镜；确认后再调用 generate_poster_and_project。'
        )

    # ② 回退：正文无确认稿格式 → LLM 按剧本生成
    from app.services.script_adaptation_service import build_script_shot_plan
    plan = build_script_shot_plan(
        db, str(novel.id), model_id=None, per_duration=15, target_total_seconds=300,
    )
    eps = plan.get('episodes') or []
    segs = [s for e in eps for s in (e.get('segments') or [])]
    tot = round(sum(float(s.get('duration') or 0) for s in segs))
    return (
        f'《{novel.title}》分镜预览已生成：{plan.get("episode_count")} 幕 / '
        f'{plan.get("segment_count")} 镜 / 约 {tot} 秒（每镜≤10s、中近特写为主）。'
        '请用户确认分镜；确认后再调用 generate_poster_and_project。'
    )


def _tool_generate_poster_and_project(db: Session, args: dict, model=None) -> str:
    """【三阶段③】生成封面（后台异步）+ 按已确认的分镜预览创建项目（复用落库）。"""
    from app.services.script_adaptation_service import materialize_from_shot_plan
    from app.tasks.generate_novel_poster import generate_novel_poster_task

    novel = _pick_novel(db, args)
    if novel is None:
        raise ValueError("未找到目标剧本，请先调用 write_script 写剧本")
    if not (novel.shot_plan or {}).get("episodes"):
        raise ValueError("还没有已确认的分镜预览，请先调用 generate_shot_preview 并请用户确认分镜")
    try:
        generate_novel_poster_task.delay(str(novel.id), model_id=None)
        poster_hint = "封面已在后台生成中"
    except Exception as e:  # noqa: BLE001
        poster_hint = "封面触发失败(可重试)。" + str(e)[:100]
    project = materialize_from_shot_plan(db, novel, per_duration=15)
    segs = [s for ep in project.episodes for s in ep.segments]
    assets_n = len(project.assets) if hasattr(project, "assets") else 0
    tot = round(sum((s.duration or 0) for s in segs))
    return (
        f"项目已创建：{project.title}（{len(project.episodes)} 幕 / {len(segs)} 镜 / "
        f"约 {tot} 秒 / {assets_n} 资产）。{poster_hint}；可在剧本库查看并进入生图/生视频。"
    )
