"""创建对齐 TraeWork 对话沟通/联网搜索能力的 Skill 记录（P4）。"""
import sys

sys.path.insert(0, ".")
from app.database import SessionLocal
from app.models.agent import AgentSkill
from sqlalchemy import select
from app.services import agent_service

db = SessionLocal()

SKILLS = [
    {
        "name": "web_fetch",
        "description": "读取网页正文内容（标题+正文，去除导航脚本）。当搜索结果只有摘要、需要获取页面详细信息时使用。",
        "prompt": "读取指定网页正文，返回标题与正文纯文本。",
        "tool_type": "builtin_tool",
        "handler": "web_fetch",
        "enabled": True,
        "sort": 12,
    },
    {
        "name": "agent_browser",
        "description": "网页信息抓取与调研：用搜索+读网页组合完成多步联网调研、信息整合与交叉验证，对齐 TraeWork agent-browser 的网页数据获取能力。",
        "prompt": (
            "你是一位专业的网页调研助手，负责完成多步联网信息检索与整合。\n"
            "执行原则：\n"
            "1. 先拆解用户问题为几个独立信息点。\n"
            "2. 对每个信息点：先调用 web_search 搜索，得到候选链接后调用 web_fetch 打开高价值页面读正文，不要只依赖搜索摘要。\n"
            "3. 同一主题用不同关键词搜 2-3 次（如中文关键词 + 英文关键词），交叉验证信息一致性。\n"
            "4. 重要结论标注来源链接；信息冲突时说明冲突并给出多方可信度判断。\n"
            "5. 输出结构化调研报告：结论摘要、关键事实（带来源）、参考资料列表。\n"
            "全程用简体中文，直接给出成果，不要复述过程。"
        ),
        "tool_type": "prompt",
        "handler": None,
        "enabled": True,
        "sort": 20,
    },
    {
        "name": "deep_research",
        "description": "深度主题调研（如某模型/技术/事件的最新资料）：多轮搜索、多源交叉、读原文，产出带引用的调研报告。",
        "prompt": (
            "你是一位深度研究员，负责对一个主题做扎实的联网调研。\n"
            "流程：\n"
            "1. 明确调研目标与关键问题清单。\n"
            "2. 逐问题检索：web_search 找线索（中英文多关键词），对权威页面用 web_fetch 读全文。\n"
            "3. 优先权威信源（官方文档、官方 GitHub、学术论文、知名媒体）。\n"
            "4. 汇总成报告：主题背景、核心发现（每条标注来源）、争议与未确认信息、进一步阅读链接。\n"
            "输出控制在 1200 字以内，简体中文，重事实、不编造。"
        ),
        "tool_type": "prompt",
        "handler": None,
        "enabled": True,
        "sort": 21,
    },
]

created = []
for s in SKILLS:
    exists = db.scalar(select(AgentSkill).where(AgentSkill.name == s["name"]))
    if exists:
        print(f"已存在，跳过: {s['name']}")
        continue
    agent_service.create_skill(db, type("P", (), s)())
    created.append(s["name"])
    print(f"创建: {s['name']}")

db.close()
print("done:", created or "（无新增）")
