# AI漫剧 · AI 短剧视频生成工作台（AIDrama Platform）

> AI 短剧 / AI 漫剧（微短剧 / 条漫风）的**一句话 → 成片**全流程创作平台。
> 从一句梗概出发，自动生成剧本 → 分镜 → 角色/场景/道具资产（一致性） → 关键帧 → 视频 → 配音/音效/BGM → 字幕 → 导出成片，并提供无限画布、导演台连续长片、AI 创作智能体等高级工作流。
>
> *A one-liner-to-episode AI short-drama production workbench. Turn a single logline into a finished episode: script → storyboard → consistent assets → keyframes → video → voice/SFX/BGM → subtitles → export, plus an infinite canvas, a "Director" continuous-film mode and an AI creation agent.*

---

## 目录 / Overview

- [中文介绍](#中文介绍)
- [English Introduction](#english-introduction)
- [架构 / Architecture](#架构--architecture)
- [快速开始 / Quick Start](#快速开始--quick-start)
- [核心模块 / Core Modules](#核心模块--core-modules)
- [技术栈 / Tech Stack](#技术栈--tech-stack)
- [许可 / License](#许可--license)

---

# 中文介绍

## 这是什么

**AI漫剧** 是一套开源的 AI 短剧 / AI 漫剧视频生成工作台。它把「剧本→画面→成片」这条原本需要多个人工角色（编剧、分镜师、美术、剪辑、配音、后期）的生产线，压缩到**一句话梗概**起步，并在一个平台上串起整条链路。

平台遵循一条核心理念：**生成一律手动触发、内容可控** —— 平台提供的是「工作台」而非「工厂」，用户对每一步生成拥有完全的控制与返工自由。

## 一句话成片流水线

```
一句话梗概
   │  (LLM)
   ▼
标题 + 剧本 + 角色清单 + 场景清单 + 分镜列表
   │  (拆解/规划)
   ▼
角色四视图 / 场景图 / 道具图（一致性锚点，跨镜头复用）
   │  (图生图)
   ▼
各分镜关键帧（角色/场景参考注入）
   │  (图生视频 R2V / I2V)
   ▼
成片视频（多镜头连续长片可整条一次性生成）
   │  (配音 + 音效 + BGM + 字幕)
   ▼
ffmpeg 混音 + 字幕硬烧 → 导出成片
```

## 核心特性

- **角色一致性**：通过「四视图 + 参考锚点」保证同一角色跨所有分镜外观稳定，是短剧审美成立的基石。
- **模型无关（Provider 抽象）**：生图 / 生视频 / 文本 / TTS 全部抽象为可插拔 Provider 接口；Agnes(OpenAI 兼容)、ComfyUI / MiniMax H3、本地 vLLM(DGX) 均可在「模型配置中心」后台填表接入。
- **多镜头连续长片（导演台）**：基于 MiniMax H3 的 AIMixer 段间引导，把一整幕的多个分镜**当作一段连续长片**一次生成，真正解决「逐镜生成再拼接」在运动矢量、机位、光色和音频上的接缝问题；按切点可自动裁回各分镜片段。
- **无限画布（Canvas）**：以节点 + 连线的方式呈现导演分镜总览，支持资产引用边、承接/衔接连线、批量生成、序列预览、画布体检；并抽象为可插拔的「生成方案体系」（单镜直出 / 多段连拍 / 纯文生）。
- **AI 创作智能体**：内置 44+ 工具（生图/生视频/剧本/小说/搜索/Git/终端/code/Skill/MCP/角色/规则/插件/记忆/规划/目标/子智能体），兼容 Skill、MCP、角色人设，支持去 AI 味写作审校。
- **成片后期全流程**：配音（TTS，支持内心独白/旁白/多语言）、音效、BGM（按幕混音）、字幕（自动对齐 / 可编辑）、九宫格运镜、超分（4x/2x 归一到 1080p）、自动成片导出。
- **小说 / 剧本双入口**：支持从长篇小说续写拆解、从剧本一键生成，或直接粘贴 / 一句话创建。
- **平台工程**：Celery 异步任务 + 心跳回收 + 取消同步远端、任务中心、模型配置中心、资产库 / 音频库复用。

## 设计原则

1. **模型无关**：所有生成能力走 Provider 接口，切换第三方 API 不动业务层。
2. **异步优先**：耗时生成全部入 Celery + Redis 队列，前端轮询 / SSE。
3. **可定向返工**：最小粒度到「单分镜 / 单帧 / 单镜头」，支持单点重跑，不重跑全流程。
4. **成本可控**：生成前预估，失败不扣费；生成一律手动触发。
5. **模型是运营态配置**：后台填表即可新增文本 / 生图 / 生视频 / TTS 模型，按用户选择的 model_id 动态路由。

---

# English Introduction

## What is this

**AIDrama** is an open-source **AI short-drama (微短剧 / AI comic-drama) video production workbench**. It compresses what used to take a whole crew — screenwriter, storyboard artist, art director, editor, voice actor, post-production — into a single platform that starts from **one logline** and drives the entire pipeline end-to-end.

The platform follows one guiding principle: **generation is always manually triggered and fully controllable** — it is a *workbench*, not an automated factory. You keep complete control and freedom to rework every step.

## One-liner-to-episode pipeline

```
One-sentence logline
   │  (LLM)
   ▼
Title + script + character list + scene list + storyboard
   │  (planning)
   ▼
Character 4-view / scene / prop assets (consistency anchors)
   │  (image-to-image)
   ▼
Keyframes per shot (with character/scene references injected)
   │  (image-to-video R2V / I2V)
   ▼
Episode video (a whole continuous film in one pass)
   │  (voice + SFX + BGM + subtitles)
   ▼
ffmpeg mixing + burn-in subtitles → final export
```

## Highlights

- **Character consistency**: "4-view + reference anchors" keep the same character visually stable across every shot.
- **Model-agnostic (Provider abstraction)**: all generation goes through a pluggable Provider interface; Agnes (OpenAI-compatible), ComfyUI / MiniMax H3, and a local vLLM (DGX) cluster can all be wired in from the admin "model config center".
- **Multi-shot continuous film (Director mode)**: built on MiniMax H3's AIMixer inter-segment guidance, rendering a whole episode's shots **as one continuous film** in a single pass — solving motion / camera / lighting / audio continuity seams; clips can be split back per shot.
- **Infinite canvas**: a node-and-edge overview for directors, with asset-reference and continuation edges, batch generation, sequence preview and a canvas health check, abstracted as a pluggable generation-scheme registry.
- **AI creation agent**: 44+ built-in tools (image/video/script/novel/search/git/terminal/code/Skill/MCP/role/rules/plugins/memory/plan/goal/subagent), Skill & MCP compatible, role personas, and an "anti-AI-flavor" writing pass.
- **Full post-production**: TTS dubbing (inner monologue / narration / multilingual), SFX, per-episode BGM mixing, auto-aligned / editable subtitles, 9-grid camera, upscaling (4x/2x to 1080p), and automatic export.
- **Novel & script entry points**: long-novel continuation, script, pasted text, or a one-liner.
- **Platform engineering**: Celery async tasks with heartbeat recovery and remote cancel-sync, a task center, model config center, reusable asset / audio libraries.

## Design principles

1. **Model-agnostic**: generation via Provider interfaces; switching third-party APIs never touches business logic.
2. **Async-first**: heavy generation goes through Celery + Redis; frontend polls / uses SSE.
3. **Targeted rework**: granularity down to a single shot / frame.
4. **Cost-aware**: estimate before generating; no charge on failure; generation is always manually triggered.
5. **Models are runtime config**: add models by filling a form; routes dynamically by the user's chosen model_id.

---

# 架构 / Architecture

```
┌────────────────────── Frontend (Vite + React 18 + TS + Tailwind) ──────────────────────┐
│ 项目 / 工作台 · 剧本分镜 · 美术资产 · 无限画布(节点+连线) · 智能体对话 · 任务中心       │
│        │ REST /api (Axios)                    │ SSE / 轮询                              │
└────────┼──────────────────────────────────────┼────────────────────────────────────────┘
         ▼                                      ▼
┌───────────────────── Backend (FastAPI) ─────────────────────┐
│  API 层 (projects/segments/assets/keyframes/videos/voice/   │
│         bgm/sfx/export/canvas/evaluate/agent/gen/… )        │
│  业务服务 (script → storyboard → asset → keyframe → video → │
│            voice → subtitle → export → longfilm → canvas)   │
└────────────────────────────┬────────────────────────────────┘
                             │ (Celery → Provider)
                             ▼
┌── Providers (pluggable) ────────────────────────────────────┐
│  openai_compatible (Agnes / local vLLM DGX) · ComfyUI (H3)  │
│  http_poll (3rd-party async)              · openai_tts       │
└───────────────┬─────────────────────────────────────────────┘
                ▼
 PostgreSQL(SQLAlchemy 2.0+Alembic) · Redis · Celery worker/beat · ComfyUI(H3) · DGX vLLM
```

- **Backend**: FastAPI + Celery + Redis + PostgreSQL (SQLAlchemy 2.0 + Alembic).
- **Frontend**: Vite + React 18 + TypeScript(strict) + Tailwind + @tanstack/react-query.
- **AI**: Agnes (OpenAI-compatible) → ComfyUI / MiniMax H3 → local vLLM (DGX Spark).
- **Sister panel**: **dashboard/** is a real-time DGX Spark dual-node GPU/vLLM monitor (Node + React + SQLite timeseries).

---

# 快速开始 / Quick Start

## Backend

```bash
docker compose up -d            # start PostgreSQL + Redis
cd backend
uv python install 3.11
uv venv -p 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env            # fill in your API keys (.env is gitignored)
alembic upgrade head
python scripts/seed_models.py

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
celery -A app.tasks.celery_app worker -l info -Q celery,video,export
```

## Frontend

```bash
cd frontend
pnpm install
cp .env.example .env.local      # match backend API_AUTH_TOKEN (optional)
pnpm dev                        # http://localhost:5173 (proxy /api, /static → :8000)
```

## Tests

```bash
cd backend && .venv/bin/python -m pytest tests
```

---

# 核心模块 / Core Modules

| 模块 | 说明（中文） | Description (EN) |
|---|---|---|
| 项目 / 剧本 | 一句话 / 粘贴 / 上传剧本 → LLM 标题+剧本+分镜 | One-liner / paste / upload → LLM draft |
| 美术资产 | 角色四视图、场景、道具，跨项目复用 | Character 4-view / scene / prop assets |
| 关键帧 | 文生图 / 图生图（参考注入，保证一致性） | Keyframes, ref-injected for consistency |
| 视频生成 | R2V / I2V，多图参考，分辨率/档位可配 | R2V / I2V video generation |
| 连续长片 | AIMixer 导演台：一集一次连续出片 | Continuous film via AIMixer Director |
| 配音/音效/BGM/字幕 | TTS、音效、按幕 BGM 混音、字幕 | TTS / SFX / BGM / subtitles |
| 导出 | ffmpeg 拼接、混音、字幕硬烧/软字幕 | ffmpeg concat / mix / subs |
| 无限画布 | 节点+连线导演分镜总览 | Infinite canvas storyboard |
| 生成方案体系 | 单镜 / 多段连拍 / 纯文生 可插拔 | Scheme registry |
| AI 智能体 | 44+ 工具、Skill/MCP/角色/记忆/规划 | Agent with 44+ tools |
| 超分 | 4x/2x 归一到 1080p | Upscale to 1080p |
| 多语言 | 对白/字幕多语种 | Multilingual dialogue/subs |
| 模型配置中心 | 后台填表接入新模型 | Admin model config |
| 系统看板 | DGX Spark 双机监控（dashboard/） | DGX cluster monitor (dashboard/) |

---

# 技术栈 / Tech Stack

- **Language / runtime**: Python 3.11 (backend), TypeScript + Node (frontend).
- **Backend**: FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, Celery, Redis, Psycopg.
- **Frontend**: Vite, React 18, TypeScript(strict), Tailwind CSS, @tanstack/react-query, @xyflow/react (canvas).
- **AI / engines**: MiniMax H3 (ComfyUI), Agnes (OpenAI-compatible), OpenAI TTS, local vLLM (DGX Spark), ffmpeg.

---

# 许可 / License

This repository's code is released under the **MIT License** (see [LICENSE](./LICENSE)).

> ⚠️ Third-party: this platform is an **orchestration / engineering layer** and ships **no model weights**. Actual generation depends on the engines you wire in (e.g. MiniMax H3, ComfyUI), each governed by its own license and commercial terms — notably the **MiniMax H3 Community License** (revenue cap / territory exclusions) and the fact that **ComfyUI is GPL-3.0** (this project only calls it over HTTP and inlines none of its code). Verify and comply with each upstream license before use / commercial deployment.

---

# 致谢 / Acknowledgments

- [MiniMax H3 ecosystem](https://github.com/wildminder/awesome-minimax-H3)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
- [@xyflow/react (React Flow)](https://github.com/xyflow/xyflow)
- FastAPI, Celery, SQLAlchemy, Vite, React, Tailwind

---

## 说明 / Notes

> 本仓库为**公开快照**，已剔除内部基础设施信息（内网 IP、服务器凭据、本机路径、API Key 等）。
> 生成引擎（MiniMax H3 / ComfyUI / DGX vLLM）本身不随仓库分发，需按需自行部署。
>
> *This repo is a public snapshot with internal infrastructure details (internal IPs, server credentials, local paths, API keys) removed. The generation engines (MiniMax H3 / ComfyUI / DGX vLLM) are not bundled and must be deployed separately.*
