"""确认稿（智能体对话 markdown 分镜稿）直落解析器。

剧本确认阶段智能体已按「场景→分镜」逐镜写好正文，每个镜头定了时长
（分镜N-M（X秒））、景别、画面、AI提示词。本解析器把确认稿原样直落为
shot_plan segments：duration 严格取稿子秒数，镜头逐镜独立不合并，运镜从
镜头文本提取（固定/推/拉/摇/移/跟）逐镜显式化，缺失默认「固定」。

2026-08-27：此前用 LLM 二次结构化（structure_episode）会合并相邻镜头、重定
时长 → 与确认稿不一致。直落优先，LLM 结构仅作回退。
"""
from __future__ import annotations
import re

from app.services.shot_grammar import SHOT_TYPES


_COLLECT_RE = re.compile(r"[（(]\s*约?\s*(\d+(?:\.\d+)?)\s*(?:秒|s|S)\s*[)）]")
_DEFAULT_DUR = 8.0
_SPEAKER_STOP = ("旁白", "镜头", "画面", "景别", "运镜", "对白", "男", "女", "司机", "乘客", "文字", "字幕", "屏幕", "手机", "微信", "电话", "广播", "收音机", "机器", "系统", "音效", "特效", "通知", "弹窗", "群聊")


# 非角色名（画外音/文字等方式性标注、播出媒介等）——绝不建人物资产
NON_CHARACTER = (
    "画外音", "旁白", "内心独白", "叙述", "文字", "字幕", "屏幕", "手机", "微信", "电话",
    "广播", "收音机", "机器", "系统", "音效", "特效", "通知", "弹窗", "群聊", "镜头", "画面",
)


def clean_name(name):
    """清洗名字：去掉「（画外音/文字）」之类的发声限定与前后空白。如 张总（画外音）→ 张总。"""
    n = str(name or "").strip()
    if not n:
        return ""
    for sep in ("（", "(", "「"):
        if sep in n:
            n = n.split(sep, 1)[0].strip()
            break
    return n


def is_non_character(name):
    """是否为不应建人物资产的名字：空 / 命中非角色词。"""
    n = clean_name(name)
    if not n:
        return True
    if n in NON_CHARACTER:
        return True
    return any(w in n for w in ("旁白", "画外音", "内心独白", "文字", "字幕", "机器", "系统", "广播"))


# 屏幕/消息/打字类特征词（命中即视为画面提示词，不进旁白）
_SCREEN_MSG = ("屏幕", "消息", "打字", "微信", "短信", "键盘", "手机", "对话框", "输入", "提示音", "通知", "发来", "弹出")


def is_likely_narration(text):
    """判断是否该保留为旁白（画外音朗读）的短叙述句。

    规则（2026-08-27 收紧）：带屏幕/消息/打字等提示词特征，或超过 60 字的长描述
    （多为环境/动作整段）→ 一律视为提示词进 description，不进旁白。
    """
    t = str(text or "").strip().strip("「」").strip()
    if not t:
        return False
    if any(m in t for m in _SCREEN_MSG):
        return False
    return len(t) <= 60


def dedupe_text(text):
    """按句读切句、子串去重（保留首次顺序）——清理「整块+分号重复半句」等冗余拼接。"""
    if not text:
        return ""
    parts = re.split(r"(?<=[。；！？\n])", text or "")
    kept, acc = [], ''
    for part in parts:
        s = part.strip()
        if not s or s in acc:
            continue
        kept.append(part)
        acc += part
    return "".join(kept).strip().strip("；，、")
_CONVO_RE = re.compile(r"「([^」]+)」")
_FIELD_RE = re.compile(r"^[-*\s]*(景别|运镜|画面|AI提示词|对白|旁白)[：:]\s*(.+)$")
_CAMERA_RULES = (
    ("推", ("推近", "推进", "推摄", "推镜头")),
    ("拉", ("拉远", "拉镜头", "后拉")),
    ("摇", ("摇镜", "摇摄", "仰摇", "俯摇")),
    ("移", ("横移", "平移", "移摄", "滑动镜头")),
    ("跟", ("跟拍", "跟随", "跟镜头", "移动跟随")),
    ("固定", ("固定机位", "固定镜头", "固定摄像", "固定")),
)
_PEOPLE = ("林浩", "陈总", "陈宇", "小张", "苏晴", "护士", "医生", "母亲", "父亲")
# 情绪判定词表：输出值必须落在 shot_grammar.EMOTIONS 枚举内（与分镜生成/前端枚举一致）。
# 2026-08-31 补齐：此前缺「欢快/史诗」判定词，导致从确认稿分词情绪识别不全、落库值可能越界。
_EMOTIONS = (
    ("紧张", ("紧张", "焦虑", "压抑", "剑拔弩张")),
    ("悲伤", ("悲伤", "失落", "心酸", "难过", "绝望", "疲惫")),
    ("愤怒", ("愤怒", "生气", "怒气")),
    ("温馨", ("温馨", "柔和", "温暖", "幸福")),
    ("恐惧", ("恐惧", "害怕", "惊恐")),
    ("震惊", ("震惊", "惊讶", "意外")),
    ("冷漠", ("冷漠", "面无表情")),
    ("欢快", ("欢快", "开心", "高兴", "雀跃", "愉快", "笑颜", "嬉闹")),
    ("史诗", ("史诗", "恢弘", "壮阔", "震撼", "磅礴", "大气")),
    ("平静", ("平静", "安静", "静谧", "从容", "平和", "祥和")),
)


def _strip_bold(s):
    return s.replace("*", "")


def _camera_of(text):
    # 2026-08-30：确认稿「运镜」字段常写单字（推/拉/摇/移/跟），精确单字直接命中；
    # 仅当整段文本就是该单字时才命中，避免把正文里的「推开」「跟随」误判为运镜。
    t = str(text or "").strip()
    if t in ("推", "拉", "摇", "移", "跟"):
        return t
    for cam, words in _CAMERA_RULES:
        for w in words:
            if w in text:
                return cam
    return "固定"


def _emotion_of(text):
    for emo, words in _EMOTIONS:
        if any(w in text for w in words):
            return emo
    return "平静"


def _scene_name(header):
    # 2026-08-30：兼容「】后」场景描述（旧格式）与「【第X场 标题】」头内标题两种写法，
    # 让常见确认稿场景头能正确落场景名（此前【第1场 标题】式取到空串）。
    after = header.split("】", 1)[-1].strip()
    if after:
        title = after
    else:
        inner = header.split("】", 1)[0]
        inner = re.sub(r"^【\s*第?[\d一二三四五六七八九十百零]+场\s*", "", inner)
        title = inner
    title = title.split("·", 1)[0].strip()
    return title or ""


def parse_storyboard(text):
    """解析确认稿 → [{index, title, synopsis, segments}]；无分镜块返回 []。"""
    episodes = []
    cur_scene_title = ""
    cur_shot = None

    def make_segment(sh):
        block = "\n".join(sh["lines"])
        shot_type = sh["desc"].get("景别", "") or next((st for st in SHOT_TYPES if st in block), "")
        # 运镜：显式「运镜」字段优先（值可能为 推近/固定镜头 等，统一映射枚举），否则从镜头文本兜底
        _cam_explicit = sh["desc"].get("运镜", "")
        camera = _camera_of(_cam_explicit) if _cam_explicit else _camera_of(block)
        desc_parts = []
        for f in ("画面", "AI提示词", "对白", "旁白"):
            v = sh["desc"].get(f)
            if v:
                desc_parts.append(v)
        dialogues = []
        narration_parts: list[str] = []
        desc_extra: list[str] = []
        for m in _CONVO_RE.finditer(block):
            before = block[: m.start()]
            rawtail = before[-16:] if before else ""
            # 先去掉（画外音）等发声限定，避免把「张总（画外音）：…」误判为旁白
            tail = re.sub(r"[（(][^）)]*[）)]", "", rawtail)
            # 2026-08-27：旁白/画外音/内心独白不进对白——从「」捕获兜底分流到 narration
            if re.search(r"(旁白|画外音|内心独白|叙述)", tail):
                # 2026-08-27：屏幕/消息等或长段环境描写 → 提示词，不读旁白
                if is_likely_narration(m.group(1)):
                    narration_parts.append(m.group(1))
                else:
                    desc_extra.append(m.group(1))
                continue
            dialogues.append({
                "speaker": _speaker_of(before),
                "text": m.group(1),
                "emotion": _emotion_of(block),
            })
        # 未用「」包裹的旁白行（旁白：xxx / 画外音：xxx）也收进 narration
        _np = re.search(r"(?:^|\n)\s*(?:旁白|画外音|内心独白|叙述)[：:]\s*([^\n]+)", block)
        if _np and _np.group(1).strip():
            _np_txt = _np.group(1).strip().strip("「」")
            if is_likely_narration(_np_txt):
                narration_parts.append(_np_txt)
            else:
                desc_extra.append(_np_txt)
        _seen = set()
        _parts = []
        for _p in narration_parts:
            _k = str(_p or "").strip().strip("「」").strip()
            if _k and _k not in _seen:
                _seen.add(_k)
                _parts.append(_k)
        _nar = "；".join(_parts)
        _base_nar = str(sh["desc"].get("旁白") or "").strip().strip("「」").strip()
        if _base_nar and not is_likely_narration(_base_nar):
            desc_extra.append(_base_nar)
            _base_nar = ""
        _all_nar = []
        for _x in (_base_nar, _nar):
            if _x and _x not in _all_nar:
                _all_nar.append(_x)
        narration = "；".join(_all_nar)
        return {
            "title": "分镜" + sh["no"],
            "shot_type": shot_type,
            "camera": camera,
            "description": " ".join(desc_parts + desc_extra) or block.strip(),
            "dialogue_lines": dialogues,
            "narration": narration,
            "duration": float(sh["dur"]),
            "characters": [x for x in dict.fromkeys(d["speaker"] for d in dialogues if d["speaker"]) if not is_non_character(x)],
            "scene": cur_scene_title,
            "props": [],
            "emotion": _emotion_of(block),
        }

    def _speaker_of(ctx):
        '''从「」前一小段文本里识别说话人：先去（画外音/文字）限定，形如「苏晚：」的尾巴最可靠，其次人名表。'''
        raw = (ctx or "")[-80:]
        tail = re.sub(r"（[^）]*）", "", raw)  # 去掉（画外音）等限定
        tail = re.sub(r"\([^)]*\)", "", tail)
        m = re.search(r"([\u4e00-\u9fff\w·]{1,8})[：:]\s*$", tail)
        ok = True
        if m and m.group(1) in _SPEAKER_STOP:
            ok = False
        if m and ok:
            from app.services.storyboard_parser import is_non_character
            cand = clean_name(m.group(1))
            if not is_non_character(cand):
                return cand
        for ppl in _PEOPLE:
            if ppl in tail:
                return ppl
        return ""

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        s = _strip_bold(line)
        # 场景头：【第X场 标题】/ 含「场」的【…】行
        if s.startswith("【") and "】" in s and "场" in s[:14]:
            if cur_shot is not None and episodes:
                episodes[-1]["segments"].append(make_segment(cur_shot))
            cur_shot = None
            cur_scene_title = _scene_name(s)
            episodes.append({"index": len(episodes), "title": cur_scene_title, "synopsis": "", "segments": []})
            continue
        # 分镜头：分镜N(-M)（X秒）——秒数可缺省
        hm = re.match(r'分镜(\d+)(?:-(\d+))?', s)
        if hm:
            if cur_shot is not None and episodes:
                episodes[-1]["segments"].append(make_segment(cur_shot))
            m = _COLLECT_RE.search(s)
            cur_shot = {
                "no": hm.group(1) + ("-" + hm.group(2) if hm.group(2) else ""),
                "dur": float(m.group(1)) if m else _DEFAULT_DUR,
                "lines": [s],
                "desc": {},
            }
            continue
        # 其余行：当前镜头续写（含字段/对白）
        if cur_shot is not None:
            cur_shot["lines"].append(s)
            fm = re.match(r'^[-*\s]*(景别|运镜|画面|AI提示词|对白|旁白)[：:]\s*(.+)$', _strip_bold(s))
            if fm and fm.group(1) not in cur_shot["desc"]:
                cur_shot["desc"][fm.group(1)] = fm.group(2).strip()
            continue
        # 尚无镜头时：形如「公司会议室 · 日 · 内」的场景描述行 → 作为场景名
        if cur_scene_title == "" and not _FIELD_RE.match(s):
            cur_scene_title = s.split("·", 1)[0].strip()
            if episodes:
                episodes[-1]["title"] = cur_scene_title

    if cur_shot is not None and episodes:
        episodes[-1]["segments"].append(make_segment(cur_shot))
    return [e for e in episodes if e["segments"]]
