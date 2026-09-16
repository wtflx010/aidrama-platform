"""ai漫剧 智能体（Agent）实现包。

A 方案重构：把 agent_service.py（6094 行单体）按「引擎内核层 / 工具注册层 /
创作对接层」三层拆分。本包承载全部实现；backend/app/services/agent_service.py
保留为兼容门面（re-export 对外符号，保证 api 层零改动）。

依赖方向（禁止反向）：
    tools/* ──► creative/*
    creative/* ──► engine/
    engine/* ──► tools/ + memory/ + agents/
    agents/* ──► engine/ + creative/
"""
