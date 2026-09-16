/** 与后端 schema 对齐的类型定义。 */

export type ProjectStatus = 'draft' | 'generating' | 'done' | 'failed'
export type MediaStatus = 'pending' | 'running' | 'succeeded' | 'failed'
export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type TaskType = 'generate_keyframe' | 'generate_video' | 'export_film'
  | 'export_episode'
  | 'generate_asset_cover' | 'generate_asset_fourview' | 'generate_voice'
  | 'generate_scene_multiview'
  | 'batch_keyframes' | 'batch_videos' | 'batch_voice' | 'batch_asset_covers'
  | 'analyze_novel' | 'adapt_script' | 'adapt_continuation' | 'generate_shot_plan'
  | 'generate_bgm' | 'generate_sfx'
  | 'generate_episode_design' | 'generate_episode_video' | 'project_director'
  | 'generate_action_sequence_template' | 'compose_action_sequence'
  | 'generate_video_draft'
  | 'write_novel'
  | 'write_script'
  | 'upscale_video'
  | 'batch_upscale_videos'
export type ModelType = 'text' | 'image' | 'video' | 'tts'
export type AssetType = 'character' | 'scene' | 'prop'
export type NovelAnalysisStatus = 'pending' | 'analyzing' | 'done' | 'failed'
export type BgmStatus = 'pending' | 'generating' | 'done' | 'failed'
export type SfxStatus = 'pending' | 'generating' | 'done' | 'failed'

export interface Project {
  id: string
  title: string
  synopsis: string | null
  script: string | null
  aspect_ratio: string
  resolution: string
  /** 项目级视频生成参数：fps/res/video_size/steps/cfg/seed/turbo（分镜级 gen_params 显式设置时优先） */
  video_params?: Record<string, unknown> | null
  status: ProjectStatus
  cover_url: string | null
  /** P4 关联预设风格（art_style.id）；为空时使用 art_style_prompt */
  style_id: string | null
  /** P4 自定义风格文本，style_id 为空时生效 */
  art_style_prompt: string | null
  /** 项目级创作规则（世界观/文风/角色约束，@项目时注入） */
  rules: string | null
  /** P6 章节续接追加：来源小说 ID（有值表示可由小说章节续接追加） */
  source_novel_id: string | null
  /** P6 已改编到的章节号（续接断点，0/空 = 未续接过） */
  processed_upto_chapter: number | null
  created_at: string
  updated_at: string
}

/** P4 屏幕尺寸可选项 */
export type AspectRatio = '16:9' | '9:16' | '1:1' | '4:3' | '3:4'

/** 视频分辨率档位（创建项目档案选择，后续生成的视频统一按此档位执行）
 * 2026-09-09 融合模型官方档：0.5mp(960×544)/0.7mp(1152×640) 为 H3 可生成档（32 倍数）；
 * 与 frontend/src/lib/videoConfig.ts / 后端 video_service._RESOLUTION_DIMS 保持同步。 */
// 0.1MP~1.0MP 统一级联档（均 32 倍数，H3 可生成）；480p/720p/768p 为后端 legacy 值（老项目兼容，不再可选）
export type VideoResolution =
  | '0.1mp' | '0.2mp' | '0.25mp' | '0.3mp' | '0.4mp' | '0.5mp'
  | '0.6mp' | '0.7mp' | '0.8mp' | '0.9mp' | '1.0mp'

/** P4 预设美术风格 */
export interface ArtStyle {
  id: string
  name: string
  category: string
  prompt_fragment: string
  description: string | null
  cover_url: string | null
  reference_images: unknown[]
  sort_order: number
  is_builtin: boolean
  created_at: string
  updated_at: string
}

/** P4 创建/更新项目时的风格选择 */
export interface ProjectStyleSelection {
  style_id: string | null
  art_style_prompt: string | null
}

export interface Episode {
  id: string
  project_id: string
  index: number
  title: string
  synopsis: string | null
  status: string
  /** P7 幕级视频：时间轴分镜描述 + 整体状态（none/running/succeeded/failed/partial） */
  video_script: string | null
  video_status: string
  /** 项目页签连续长片（一集一条无缝整片，AIMixer 导演台段间衔接） */
  continuous_film_url: string | null
  continuous_film_duration: number | null
  continuous_film_status: string
  created_at: string
  updated_at: string
}

/** P7.6 幕级视频（每幕 1 行，帧数按每幕目标时长 5/10/15/18s） */
export interface EpisodeVideo {
  id: string
  episode_id: string
  index: number
  prompt: string | null
  num_frames: number
  frame_rate: number
  width: number
  height: number
  first_frame_url: string | null
  last_frame_url: string | null
  video_url: string | null
  duration: number | null
  status: MediaStatus
  task_id: string | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface DialogueLine {
  speaker: string
  text: string
  emotion: string | null
  character_id: string | null
}

export interface Segment {
  id: string
  episode_id: string
  index: number
  title: string | null
  /** 2026-08-22 视频生成参数：{cfg, steps, seed, fps, res, reference_src, custom_first_frame_url} */
  gen_params?: Record<string, unknown>
  shot_type: string | null
  camera: string | null
  description: string | null
  dialogue: string | null
  narration: string | null
  duration: number
  character_ids: string[]
  scene_id: string | null
  prop_ids: string[]
  locked: boolean
  /** P2 结构化对白（权威源，dialogue 字符串为兼容展示） */
  dialogue_lines: DialogueLine[]
  /** 本镜整体氛围：平静/紧张/悲伤/温馨/愤怒/欢快/恐惧/史诗/冷漠/震惊 */
  emotion: string | null
  /** 2026-08-10 动作序列标记（打斗/动作段连续分镜同一标记，如 as_1） */
  action_sequence: string | null
  /** 2026-08-28 分镜内多镜头运镜节拍：[{start_sec, end_sec, shot_type, camera, content}]，空=整镜单镜头 */
  shot_beats?: ShotBeat[]
  created_at: string
  updated_at: string
}

/** 2026-08-28 分镜内多镜头运镜节拍：一段时间的画面 + 景别 + 运镜（时间连续覆盖 0~duration） */
export interface ShotBeat {
  start_sec: number
  end_sec: number
  shot_type: string
  camera: string
  content: string
}

/** 2026-08-10 白模故事版：动作序列（多镜头完整动作展示，手动触发） */
export interface ActionSequence {
  id: string
  episode_id: string
  sequence_key: string
  segment_ids: string[]
  template_url: string | null
  grid_count: number
  groups: Array<{
    cells: number[]
    segment_indexes: number[]
    shot_type: string
    camera: string
    prompt: string
  }>
  videos_url: (string | null)[]
  composed_url: string | null
  composed_duration: number | null
  status: MediaStatus
  error: string | null
  created_at: string
  updated_at: string
}

export interface Keyframe {
  id: string
  segment_id: string
  index: number
  prompt: string
  image_url: string | null
  status: MediaStatus
  used_as_video_first_frame: boolean
  model_id: string | null
  task_id: string | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface VideoClip {
  id: string
  segment_id: string
  keyframe_id: string | null
  prompt: string | null
  num_frames: number
  frame_rate: number
  width: number
  height: number
  first_frame_url: string | null
  last_frame_url: string | null
  video_url: string | null
  duration: number | null
  status: MediaStatus
  model_id: string
  task_id: string | null
  error: string | null
  /** 是否为 480p 原片超分后的高清版（超分产物；播放/导出优先取它） */
  is_upscaled?: boolean | null
  /** 超分来源的原片 clip id（is_upscaled=true 时有值） */
  upscale_of_id?: string | null
  created_at: string
  updated_at: string
}

export interface Task {
  id: string
  project_id: string
  type: TaskType
  target_type: string
  target_id: string
  model_id: string | null
  status: TaskStatus
  progress: number
  error: string | null
  result_url: string | null
  provider: string | null
  provider_task_id: string | null
  poll_url: string | null
  started_at: string | null
  finished_at: string | null
  last_heartbeat_at: string | null
  created_at: string
  updated_at: string
  /** 分镜级任务的坐标（生成视频/关键帧）：{episode_index, segment_index, title, description} */
  segment_ref?: { episode_index: number | null; segment_index: number | null; title: string | null; description?: string | null } | null
}

export interface GenerateResp {
  keyframe: Keyframe | null
  video: VideoClip | null
  task: Task
}

/** 剧集导出状态：该幕最新导出任务 + 可导出性判断（GET /episodes/{id}/export） */
export interface EpisodeExportOut {
  task: Task | null
  exportable: boolean
  reason: string | null
  file_size: number | null
}

/** 角色声线档案 / 项目旁白声线共用结构。 */
export interface VoiceProfile {
  gender: string | null
  age_group: string | null
  timbre_tags: string[]
  reference_audio_url: string | null
  reference_audio_text: string | null
  default_emotion: string | null
  voice_description: string | null
}

export interface Asset {
  id: string
  project_id: string | null
  /** 2026-08-22 全局资产库：资产绑定的项目 id 列表（含归属项目） */
  project_ids: string[]
  type: AssetType
  name: string
  description: string | null
  cover_url: string | null
  /** 四格合一四视图（CharacterSheet LoRA 封面驱动，2026-08-09）：单张横向四格图 */
  character_sheet_url: string | null
  /** 场景多视角六格合一图（POV 人物视角，2026-08-10）：仅 scene 类型 */
  scene_sheet_url: string | null
  /** 场景多视角机位组（2026-08-18 v5）：list[{"name","view_text"}] 六格渲染格式；仅 scene 填 */
  scene_shots: Array<{ name?: string; view_text?: string }>
  four_view_urls: string[]
  states: unknown[]
  reference_images: unknown[]
  art_versions: unknown[]
  expanded_description: string | null
  /** P2 角色声线档案（仅 type=character 时有意义） */
  voice_profile: VoiceProfile
  /** 方案A：资产生成时的项目生效风格指纹（style_id / style_prompt），跨项目复用一致性判断 */
  style_fingerprint?: { style_id: string | null; style_prompt: string | null } | null
  model_id: string | null
  task_id: string | null
  status: MediaStatus
  error: string | null
  created_at: string
  updated_at: string
}

export interface AssetCreate {
  type: AssetType
  name: string
  description?: string | null
}

export interface EpisodeCreate {
  title?: string
  synopsis?: string | null
  index?: number | null
}

export interface SegmentAssetBinding {
  character_ids?: string[]
  scene_id?: string | null
  prop_ids?: string[]
}

export interface AssetGenerateResp {
  asset: Asset
  task: Task
}

export interface VoiceLine {
  id: string
  segment_id: string
  text: string
  voice_id: string | null
  audio_url: string | null
  duration: number | null
  status: MediaStatus
  model_id: string | null
  task_id: string | null
  error: string | null
  /** P2 差异化配音：说话人角色 ID（旁白为 null） */
  character_id: string | null
  /** 该条对白的情绪标签 */
  emotion: string | null
  /** CosyVoice instruct 自然语言指令（emotion 映射后的最终指令） */
  instruct_text: string | null
  /** 同 segment 内多线的顺序 */
  line_index: number | null
  /** 是否为旁白 */
  is_narration: boolean
  created_at: string
  updated_at: string
}

export interface VoiceGenerateResp {
  voiceline: VoiceLine
  task: Task
}

export interface VoiceBatchGenerateResp {
  task: Task
  total: number
  sub_task_ids: string[]
}

export interface RecommendVoiceResp {
  asset_id: string
  voice_profile: VoiceProfile
}

export interface NarratorProfileOut {
  narrator_profile: VoiceProfile
}

/** 预置声音（CosyVoice wrapper 注册的预设声线） */
export interface VoicePreset {
  voice_id: string
  gender: string
  age_group: string
  sample_text: string
}

/** 音频上传返回 */
export interface AudioUploadResp {
  url: string
}

export interface Subtitle {
  id: string
  segment_id: string
  text: string
  start_ms: number
  end_ms: number
  created_at: string
  updated_at: string
}

export interface BatchResp {
  task: Task
  total: number
  dispatched_ids: string[]
}

export interface Model {
  id: string
  name: string
  provider_type: string
  model_type: ModelType
  provider_name: string
  endpoint: string
  api_key_ref: string
  model_id: string
  capability: Record<string, unknown>
  credits_per_unit: number
  scene_codes: string[]
  is_enabled: boolean
  is_default: boolean
  sort: number
  http_poll_config: Record<string, unknown> | null
}

// ===== P3 剧本库（剧本文档）=====

export interface NovelChapterSummary {
  index: number
  title: string
  summary: string
  characters: string[]
  scenes: string[]
  emotion: string
  intensity: number
  key_events: string[]
}

export interface NovelCharacter {
  name: string
  role: string
  personality: string
  appearance: string
  arc: string
}

export interface NovelScene {
  name: string
  description: string
  mood: string
}

export interface NovelAnalysisResult {
  outline?: string
  characters?: NovelCharacter[]
  scenes?: NovelScene[]
  emotion_curve?: Array<{ chapter: number; emotion: string; intensity: number }>
  core_conflict?: { protagonist: string; antagonist: string; stake: string }
  chapters_summary?: NovelChapterSummary[]
  [k: string]: unknown
}

export interface Novel {
  id: string
  title: string
  chapters_count: number
  word_count: number
  analysis_status: NovelAnalysisStatus
  error: string | null
  project_id: string | null
  created_at: string
  /** 2026-08-23 剧本海报（写剧本时自动生成，剧本库卡片海报视图） */
  poster_url: string | null
}

export interface NovelDetail extends Novel {
  analysis_result: NovelAnalysisResult | null
  raw_text: string | null
  /** 2026-08-23 确认式生成项目：写剧本后预生成的分镜预览（确认后才建项目） */
  shot_plan:
    | {
        episodes: Array<{
          index: number
          title: string
          synopsis?: string
          segments: Array<{
            index?: number
            title?: string | null
            shot_type?: string | null
            camera?: string | null
            description?: string | null
            duration?: number | null
            shot_beats?: ShotBeat[]
            narration?: string | null
            dialogue_lines?: unknown[]
            characters?: string[]
            scene?: string | string[]
            props?: string[]
          }>
        }>
        episode_count: number
        segment_count: number
        /** 2026-08-30 分镜来源：direct=按剧本已写好的分镜直落；llm=AI 生成 */
        source?: 'direct' | 'llm' | string
      }
    | null
}

/** P6 章节列表项（相对某项目标记是否已追加） */
export interface NovelChapter {
  index: number
  title: string
  processed: boolean
}

// ===== P3 BGM / SFX =====

export interface BgmTrack {
  id: string
  project_id: string
  episode_id: string | null
  emotion: string
  prompt: string
  audio_url: string | null
  duration: number
  volume: number
  status: BgmStatus
  error: string | null
  source: string
  created_at: string
  updated_at: string
}

export interface SfxClip {
  id: string
  segment_id: string
  sfx_type: string
  sfx_name: string
  audio_url: string | null
  start_time: number
  duration: number
  volume: number
  status: SfxStatus
  error: string | null
  source: string
  created_at: string
  updated_at: string
}

// ===== AI 视频页签（Video Lab，2026-08-11）=====

export interface VideoDraft {
  id: string
  project_id: string
  prompt: string
  negative_prompt: string | null
  enhanced_prompt: string | null
  first_frame_url: string | null
  last_frame_url: string | null
  ref_image_urls: string[]
  ref_video_urls: string[]
  asset_refs: string[]
  aspect_ratio: string
  duration: number
  /** 生成档位（0.1MP~1.0MP 级联档，null=跟随项目）；768p 为 legacy 行，标记为二采产物 */
  resolution: string | null
  /** 二采来源草稿 id（非二采行为 null） */
  base_draft_id: string | null
  video_url: string | null
  status: MediaStatus
  model_id: string | null
  task_id: string | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface VideoDraftCreate {
  project_id?: string | null
  prompt: string
  negative_prompt?: string | null
  first_frame_url?: string | null
  last_frame_url?: string | null
  ref_image_urls?: string[]
  ref_video_urls?: string[]
  asset_refs?: string[]
  aspect_ratio: string
  duration: number
  /** 生成档位（0.1MP~1.0MP 级联档），缺省跟随项目；480p/720p/768p 为 legacy */
  resolution?: string | null
}

export interface VideoDraftEnhanceBody extends VideoDraftCreate {}

export interface VideoDraftEnhanceOut {
  enhanced_prompt: string
  negative_prompt: string
}

export interface VideoDraftGenerateResp {
  task_id: string
}

export interface VideoDraftRegenResp {
  draft_id: string
  task_id: string
}

// ===== 创作助手（Agent，2026-08-11）=====

export interface AgentSession {
  id: string
  title: string
  /** P9 会话级工具白名单（null=全部工具可用；数组=仅白名单内工具可用） */
  tool_whitelist?: string[] | null
  created_at: string
  updated_at: string
}

/** P8 Phase 3 跨会话搜索命中项 */
export interface AgentSearchHit {
  session_id: string
  session_title: string
  message_id: string
  role: 'user' | 'assistant'
  snippet: string
  created_at: string | null
}

/** P8 Phase 5 多模态附件处理结果（POST /agent/attachments 返回） */
export interface AgentAttachment {
  kind: 'image' | 'audio' | 'video' | 'document'
  name: string
  url: string
  /** 音频：Whisper 转写文本 */
  transcript?: string
  /** 视频：抽帧图片 URL 列表 */
  frames?: string[]
  /** 文档：提取的文本内容 */
  text?: string
}

export interface AgentMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  /** 推理模型思考过程（assistant 消息携带，前端折叠展示） */
  thinking?: string | null
  /** 流式增量落库标记：false=生成中（切走后回放显示「思考中」并轮询直至 true） */
  completed?: boolean
  images: string[] | null
  /** P9 多模态附件持久化（kind/name/url/transcript/frames/text），历史消息展示与回放 */
  attachments?: AgentAttachment[] | null
  tool_name: string | null
  tool_params: Record<string, unknown> | null
  tool_status: 'running' | 'succeeded' | 'failed' | null
  media_urls: string[] | null
  project_id: string | null
  created_at: string
}

export interface AgentSkill {
  id: string
  name: string
  description: string
  prompt: string
  tool_type: string
  handler: string | null
  is_builtin: boolean
  enabled: boolean
  sort: number
  created_at: string
}

/** MCP 服务器配置（stdio 本地进程） */
export interface AgentMcpServer {
  id: string
  name: string
  description: string
  command: string
  args: string[] | null
  env: Record<string, string> | null
  enabled: boolean
  sort: number
  created_at: string
}

/** MCP 连接测试结果 */
export interface AgentMcpTestResult {
  ok: boolean
  message: string
  tools: { type: string; function: { name: string; description: string; parameters: Record<string, unknown> } }[]
}

/** 生成插件（对齐 TraeWork 插件：选择插件 → 描述需求 → 执行） */
export interface AgentPlugin {
  id: string
  name: string
  label: string
  description: string
  prompt: string
  tool: string
  mode: string
  is_builtin: boolean
  enabled: boolean
  sort: number
  created_at: string
}

/** 创作助手规则（对齐 TraeWork Rules：全局/项目级规则自动注入对话） */
export interface AgentRule {
  id: string
  name: string
  content: string
  scope: 'global' | 'project'
  project_id: string | null
  enabled: boolean
  sort: number
  created_at: string
}

/** P8 定时自动化任务（对齐 Hermes/OpenClaw cron） */
export interface AgentSchedule {
  id: string
  name: string
  cron_expr: string
  action_type: 'prompt' | 'system'
  prompt: string
  system_action: string
  session_id: string | null
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  run_count: number
  last_error: string | null
  created_at: string
}

/** Plan 工作流：创作规划文档 */
export interface AgentPlanStep {
  title: string
  description: string
  status: 'pending' | 'done'
}

export interface AgentPlan {
  id: string
  session_id: string
  title: string
  content: string
  steps: AgentPlanStep[] | null
  status: 'draft' | 'confirmed' | 'done'
  project_id: string | null
  created_at: string
  updated_at: string
}

/** 创作助手长期记忆 */
export interface AgentMemory {
  id: string
  scope: 'global' | 'project'
  content: string
  project_id: string | null
  created_at: string
}

/** P9 记忆语义检索命中项 */
export interface AgentMemoryHit {
  id: string
  scope: 'global' | 'project'
  content: string
  score: number
}

/** 创作目标（Goal 工作流） */
export interface AgentGoal {
  id: string
  session_id: string
  title: string
  goal: string
  criteria: string
  status: 'active' | 'paused' | 'done'
  progress_summary: string
  step_count: number
  project_id: string | null
  created_at: string
  updated_at: string
}

/** SSE 流式对话事件 */
export interface AgentSSEEvent {
  type: 'token' | 'thinking' | 'tool' | 'media' | 'project' | 'done' | 'error'
  content?: string
  name?: string
  status?: string
  step?: string
  kind?: 'image' | 'video' | 'audio'
  url?: string
  id?: string
  title?: string
  draft_id?: string
  task_id?: string
  /** write_novel 提交后的事件携带（novel_id 供续写复用） */
  novel_id?: string
  /** P9 子智能体并行化：同一轮 ≥2 个子智能体并行执行（running 事件携带） */
  parallel?: boolean
  message_id?: string
  message?: string
  context_tokens?: number
  /** P8 git_commit 待确认信息（status=pending 时携带） */
  pending_git_commit?: { files?: string[]; message?: string }
  /** P9 git_push 待确认信息（status=pending 时携带） */
  pending_git_push?: { remote?: string; branch?: string }
  /** P9.5 项目草案待确认载荷（create_project status=pending 时携带） */
  pending_project_draft?: {
    draft: Record<string, unknown>
    summary: string
    session_id: string
    title: string
    default: { aspect_ratio: string; style_id: string | null; art_style_prompt: string | null }
  }
  /** 项目删除待确认载荷（project_delete status=pending 时携带） */
  pending_project_delete?: {
    project_id: string
    title: string
    detail: string
  }
}

/** @ 引用的上下文资源（项目/剧本文档/历史会话/资产） */
export interface ContextRef {
  type: 'project' | 'document' | 'session' | 'asset'
  id: string
  label: string
}

// ===== 长篇小说写作（/novel 工作流，2026-08-11）=====

export interface NovelOutlineChapter {
  index: number
  title: string
  brief: string
}

/** /novel 命令生成的小说大纲（预览确认后提交写作任务） */
export interface NovelOutline {
  title: string
  genre: string
  logline: string
  world: string
  chapters: NovelOutlineChapter[]
}

/** novel-write 提交返回 */
export interface NovelWriteResp {
  novel_id: string
  task_id: string
  title: string
}

// ===== 创作角色配置（T2，2026-08-11）=====

export interface AgentRole {
  id: string
  name: string
  role_key: string
  kind: 'chat' | 'subagent'  // chat=对话角色 / subagent=子智能体
  persona: string
  tools: string[] | null
  enabled: boolean
  sort: number
  created_at: string
}
