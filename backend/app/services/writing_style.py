"""去AI味写作规范 · 提示词注入层数据源。

与 docs/去AI味写作规范.md 保持同一事实来源。
用途：生成提示词（大纲/正文/分镜）注入硬规则与文风锚定；humanize_service 复用此处黑名单做检测。
"""

# ── 文风档位 ────────────────────────────────────────────────┐
STYLE_NETWORK = "网文爽感"
STYLE_LIFE = "生活流"
STYLE_FILM = "电影感"
STYLE_MODES = (STYLE_NETWORK, STYLE_LIFE, STYLE_FILM)

STYLE_MODE_SPECS = {
    STYLE_NETWORK: {
        "name": STYLE_NETWORK,
        "summary": "节奏快、反转密、爽点前置，适合泛娱乐短剧/爽文",
        "rules": (
            "文风：网文爽感档。节奏快、反转密，开篇 3 句内进事件，每集/每章至少一个钩子或反转。",
            "语感：短句、白话、口语，禁止慢镜头抒情与超过 2 句的环境铺陈。",
            "对白：短促、带情绪、冲突即时爆发，角色可有高频口头禅。",
        ),
        "anchor": "阿珍把菜刀往案板上一拍。\n「你再说一遍。」\n男人没敢动，喉结滚了一下。\n「妈，她说她……不嫁了。」",
    },
    STYLE_LIFE: {
        "name": STYLE_LIFE,
        "summary": "真实、琐碎、克制的日常质感，适合家庭/情感/职场题材",
        "rules": (
            "文风：生活流档。细节密集但不煽情，宁平勿惊，禁止戏剧化大词与巧合堆砌。",
            "语感：常见答非所问、说话绕弯，常用「嗯」「再说吧」回避。",
            "禁忌：每场都升华、台词说教、情绪的直白宣告。",
        ),
        "anchor": "锅里的水开了，她没动，看着蒸汽把窗玻璃罩白。\n「今天还回来吃饭吗？」\n电话那头顿了一下。\n「……嗯，加班。」\n她把火关了。",
    },
    STYLE_FILM: {
        "name": STYLE_FILM,
        "summary": "视听语言优先，适合强调画面质感与氛围的项目（如漫剧出片）",
        "rules": (
            "文风：电影感档。描写有镜头感（构图/光线/运镜视角），状态用画面呈现而非说明。",
            "对白：简短留白，靠动作与停顿推进张力，允许长沉默。",
            "禁忌：旁白解释情绪、对白说教、单句台词超过 20 字。",
        ),
        "anchor": "走廊尽头的灯闪了两下，灭了。\n她站在光与暗的边界，没回头。\n身后的脚步声越来越近。\n「你知道了。」他说。\n她没答，抬手把钥匙留在了门锁上。",
    },
}


def get_style_mode(value):
    """归一化文风档位：空值/未知值 → 默认档（网文爽感）。"""
    if value in STYLE_MODES:
        return value
    return STYLE_NETWORK


def style_summary(style_mode):
    return STYLE_MODE_SPECS.get(get_style_mode(style_mode), {}).get("summary", "")


def _style_block(style_mode, include_anchor=True):
    """组合可注入提示词的文风块：档位名 + 规则 + 风格锚定样本。"""
    spec = STYLE_MODE_SPECS.get(get_style_mode(style_mode), STYLE_MODE_SPECS[STYLE_NETWORK])
    lines = ["【文风档位】" + spec["name"] + "。" + spec["summary"]]
    lines.extend(spec["rules"])
    if include_anchor and spec.get("anchor"):
        lines.append("【风格锚定样本（模仿其语感，不是照抄情节）】")
        lines.append(spec["anchor"])
    return "\n".join(lines)


# ── 去AI味硬规则（黑名单 + 人类化要求）────────────────────────┐
AI_MARKER_TERMS = (
    "仿佛", "宛如", "犹如", "氤氲", "凝视", "眸", "泪光", "复杂",
    "决然", "决绝地", "毅然", "嘴角", "深吸一口气", "愣住了", "沉默了",
    "空气", "喧嚣", "命运", "宿命", "或许这就是", "生活就像",
    "时间仿佛", "此刻", "在那一刻", "心中涌起", "眼底", "微微扬起",
)

DEAI_RULES_TEXT = """【去AI味硬规则·必须遵守】
1. 禁止模板化起句：不得以时空感叹或万能开头起笔（如「在这个喧嚣的世界里」「时间仿佛凝固」）。
2. 禁止三连排比与「越…越…」句群；禁止格式对仗的句子两两出现。
3. 禁止情绪汇报：不得直接贴标签（如「眼中闪过一丝复杂的情绪」「眸色一沉」），状态必须用动作/物件/细节演出来。
4. 禁止空泛升华：结尾不得强行点题或金句总结（如「生活就像…」「或许这就是…的意义」）。
5. 禁止书面腔入侵：对白与旁白不得使用「仿佛/宛如/氤氲/凝视」等书面词。
6. 禁止万能反应：不得出现「她愣住了」「他沉默了良久」「空气安静得可怕」这类空模板。
7. 留白与克制：情绪越重越要省着写，高潮处截断，不替读者/观众抒情。
8. 对白性格化：每个角色口吻可区分（口头禅/句长/用词），话不说满，允许被打断与未尽的话。
9. 具体细节：用具体的物件、动作、环境细节表达状态，禁止抽象概括。
10. 节奏呼吸：长短句交替制造停顿，同一段落不得连续三个同构句。"""


def build_inject_block(style_mode, include_anchor=True, include_deai=True):
    """返回一次生成所需的完整注入块（去AI味硬规则 + 文风档位）。"""
    parts = []
    if include_deai:
        parts.append(DEAI_RULES_TEXT)
    parts.append(_style_block(style_mode, include_anchor=include_anchor))
    return "\n\n".join(parts)


def humanize_style_hint(style_mode):
    """审校 prompt 用：一句话说明按哪档标准审校。"""
    spec = STYLE_MODE_SPECS.get(get_style_mode(style_mode), STYLE_MODE_SPECS[STYLE_NETWORK])
    return f"按文风《{spec['name']}》标准审校：{spec['summary']}。"
