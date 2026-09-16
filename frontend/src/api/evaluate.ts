/** 成片评估（P0-1）与角色一致性（P0-2）API 封装。 */
import { api } from './client'

export interface EpisodeEvalScores {
  hook: number
  attention: number
  retention: number
  virality: number
  overall: number
}

export interface EvalSuggestion {
  segment_index: number
  issue: string
  suggestion: string
}

export interface EpisodeEvalOut {
  id: string
  episode_id: string
  video_url: string | null
  scores: EpisodeEvalScores
  rule_scores: Record<string, unknown>
  report: string | null
  suggestions: EvalSuggestion[]
  status: string
  error: string | null
  created_at: string | null
}

export interface EvaluateTriggerResp {
  task_id: string
  episode_id: string
}

const epPath = (pid: string, eid: string) => `/projects/${pid}/episodes/${eid}`

export const evaluateApi = {
  /** 触发一集成片评估（异步任务），返回 task_id 供 useTaskPoller 轮询 */
  trigger: (pid: string, eid: string) =>
    api.post<EvaluateTriggerResp>(epPath(pid, eid) + '/evaluate'),
  /** 拉取该幕最新评估结果（无则 null） */
  get: (pid: string, eid: string) =>
    api.get<EpisodeEvalOut | null>(epPath(pid, eid) + '/evaluate'),
  /** 应用评估建议：清弱镜提示词缓存 + 可选重派关键帧，返回处理结果 */
  applyFeedback: (pid: string, eid: string, segmentIndexes?: number[]) =>
    api.post<{ ok: boolean; applied: { segment_index: number; keyframe_task_id: string | null }[]; message: string }>(
      epPath(pid, eid) + '/evaluate/apply-feedback',
      { segment_indexes: segmentIndexes ?? null, regenerate_keyframes: true },
    ),
  /** 角色一致性总览（消费 H3 retention_analysis，标出弱保留分镜） */
  consistency: (pid: string, eid: string) =>
    api.get<{
      episode_id: string
      segments: { segment_index: number; score: number | null; weak: number[]; source: string }[]
      weak_segments: { segment_index: number; score: number; weak: number[] }[]
      overall_score: number | null
      evaluated_segments: number
      total_segments: number
    }>(epPath(pid, eid) + '/consistency'),
}

/** 四维评分 → 中文标签 + 语义色（看板展示用） */
export const DIMENSIONS: { key: keyof EpisodeEvalScores; label: string; hint: string }[] = [
  { key: 'hook', label: '开头钩子', hint: '前 3 秒是否抓人' },
  { key: 'attention', label: '注意力', hint: '能否持续拉住观众' },
  { key: 'retention', label: '留存', hint: '是否看完并期待下集' },
  { key: 'virality', label: '病毒性', hint: '易被分享/讨论/二创' },
  { key: 'overall', label: '综合', hint: '整体成片质量' },
]

export function scoreColor(v: number | undefined): string {
  const s = Number(v ?? 0)
  if (s >= 8) return 'bg-emerald-500'
  if (s >= 6) return 'bg-amber-500'
  return 'bg-rose-500'
}
