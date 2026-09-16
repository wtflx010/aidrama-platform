/** 多语言出海（P2-5）与修片（P1-4）API 封装。 */
import { api } from './client'
import type { EpisodeVideo } from './types'

export const episodeVideosApi = {
  /** 取一幕已生成视频片段列表（供修片选源视频） */
  getByEpisode: (eid: string) => api.get<EpisodeVideo[]>(`/episodes/${eid}/videos`),
}

// ── 多语言 ─────────────────────────────────────────────
/** 可用目标语言（ISO 639-3，与后端 dub_episode 白名单一致） */
export const LANGUAGES: { code: string; label: string }[] = [
  { code: 'eng', label: 'English (英语)' },
  { code: 'spa', label: 'Español (西语)' },
  { code: 'fra', label: 'Français (法语)' },
  { code: 'deu', label: 'Deutsch (德语)' },
  { code: 'ita', label: 'Italiano (意语)' },
  { code: 'por', label: 'Português (葡语)' },
  { code: 'rus', label: 'Русский (俄语)' },
  { code: 'jpn', label: '日本語 (日语)' },
  { code: 'kor', label: '한국어 (韩语)' },
  { code: 'vie', label: 'Tiếng Việt (越南语)' },
  { code: 'tha', label: 'ไทย (泰语)' },
  { code: 'ara', label: 'العربية (阿语)' },
  { code: 'hin', label: 'हिन्दी (印地语)' },
  { code: 'ind', label: 'Bahasa Indonesia (印尼语)' },
  { code: 'msa', label: 'Bahasa Melayu (马来语)' },
  { code: 'chi', label: '中文 (简体)' },
  { code: 'yue', label: '粤語' },
]

export interface MultilingualTriggerResp {
  task_id: string
  episode_id: string
  target_language: string
}

export const multilingualApi = {
  /** 触发一集多语言翻译/配音/SRT 导出 */
  trigger: (pid: string, eid: string, target_language: string, do_tts: boolean) =>
    api.post<MultilingualTriggerResp>(
      `/projects/${pid}/episodes/${eid}/multilingual`,
      { target_language, do_tts },
    ),
}

// ── 修片 ─────────────────────────────────────────────
export interface ReworkResp {
  task_id: string
}

export interface ReframeIn {
  video_url: string
  target_ratio: string
  resolution: string
}
export interface VoiceChangeIn {
  video_url: string
  text: string
  voice_id?: string | null
  emotion?: string | null
}

export const ASPECT_RATIOS: { value: string; label: string }[] = [
  { value: '9:16', label: '9:16 竖版' },
  { value: '16:9', label: '16:9 横屏' },
  { value: '1:1', label: '1:1 方形' },
  { value: '4:3', label: '4:3' },
  { value: '3:4', label: '3:4 竖版' },
]
export const RESOLUTIONS: { value: string; label: string }[] = [
  { value: '480p', label: '480p' },
  { value: '720p', label: '720p' },
  { value: '1080p', label: '1080p' },
]

export interface DrawToVideoIn {
  video_url: string
  prompt: string
  sketch_url?: string | null
  ratio?: string
}

export const reworkApi = {
  reframe: (body: ReframeIn) => api.post<ReworkResp>('/rework/reframe', body),
  voiceChange: (body: VoiceChangeIn) => api.post<ReworkResp>('/rework/voice-change', body),
  /** 局部重绘出片（H3 Ref2VA 近似）：源视频 + 编辑后草图帧 + 提示词 */
  drawToVideo: (body: DrawToVideoIn) => api.post<ReworkResp>('/rework/draw-to-video', body),
}
