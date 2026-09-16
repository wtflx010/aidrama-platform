/** 画布(生图工作台)API:文档 CRUD + 回滚 + 分镜导入 + 批量生成。 */
import { api } from './client'
import type { CanvasEdge, CanvasNode } from '../lib/canvasTypes'

export interface CanvasBoardDocument {
  nodes: CanvasNode[]
  edges: CanvasEdge[]
}

export interface CanvasBoardDto {
  id: string
  project_id: string | null
  name: string
  document: CanvasBoardDocument
  version: number
  created_at: string
  updated_at: string
}

export interface CanvasNodeGenerateOut {
  node_id: string
  task_id: string | null
  keyframe_id: string | null
  error: string | null
}

export interface CanvasBoardGenerateOut {
  task_id: string
  nodes: CanvasNodeGenerateOut[]
}

export interface ProjectLite {
  id: string
  title: string
}

export interface SegmentLite {
  id: string
  episode_id: string
  index: number
  description: string | null
  shot_type: string | null
}

export function listBoards(): Promise<CanvasBoardDto[]> {
  return api.get('/canvas')
}

export function createBoard(body: { name?: string; project_id?: string | null }): Promise<CanvasBoardDto> {
  return api.post('/canvas', body)
}

export function getBoard(id: string): Promise<CanvasBoardDto> {
  return api.get(`/canvas/${id}`)
}

export function saveBoard(id: string, body: { document: CanvasBoardDocument; name?: string }): Promise<CanvasBoardDto> {
  return api.put(`/canvas/${id}`, body)
}

export function rollbackBoard(id: string): Promise<CanvasBoardDto> {
  return api.post(`/canvas/${id}/rollback`)
}

export function deleteBoard(id: string): Promise<void> {
  return api.del(`/canvas/${id}`)
}

export function importBoardFromSegment(segmentId: string, name?: string): Promise<CanvasBoardDto> {
  return api.post('/canvas/import-segment', { segment_id: segmentId, name })
}

export function importBoardsFromSegments(segmentIds: string[], name?: string): Promise<CanvasBoardDto> {
  return api.post('/canvas/import-segments', { segment_ids: segmentIds, name })
}

export function updateSegmentCompositionPoint(segmentId: string, point: string): Promise<unknown> {
  return api.put(`/segments/${segmentId}`, { composition_point: point })
}

export function generateBoard(
  id: string,
  body: { node_ids: string[]; model_id?: string; ratio?: string; kind?: 'image' | 'video' },
): Promise<CanvasBoardGenerateOut> {
  return api.post(`/canvas/${id}/generate`, body)
}

// ---- 导演台模式（多段连续生视频,2026-08-29）----
export interface DirectorShotIn {
  node_id: string
  prompt?: string
  duration_sec?: number
  from_prev?: boolean
}

export interface DirectorGenerateBody {
  node_ids: string[]
  config?: {
    task_type?: string
    ratio?: string
    res?: string
    fps?: number
    steps?: number
    sampler?: string
    scheduler?: string
    shift_video?: number
    shift_audio?: number
    cfg?: number
    seed?: number
    context_enabled?: boolean
    context_frames?: number
    global_prompt?: string
    model_id?: string
    /** ComfyUI 式方案节点自身 id（2026-08-30）：后端把产物回写到该节点做节点内预览 */
    scheme_node_id?: string
  }
  shots?: DirectorShotIn[]
}

export interface DirectorGenerateOut {
  task_id: string
  board_id: string
  node_ids: string[]
  message?: string
}

export function directorGenerate(
  id: string,
  body: DirectorGenerateBody,
): Promise<DirectorGenerateOut> {
  return api.post(`/canvas/${id}/director-generate`, body)
}

// ---- 画布生成方案体系（2026-08-29）：选择方案 → 统一执行 ----
export interface SchemeMeta {
  key: string
  label: string
  description: string
  input_kind: 'node-batch' | 'director-segments' | string
}

export interface SchemeGenerateOut {
  task_id: string
  board_id: string
  scheme: string
  node_ids: string[]
  nodes: Array<Record<string, unknown>>
  message?: string
}

export function listCanvasSchemes(): Promise<SchemeMeta[]> {
  return api.get('/canvas/schemes')
}

export function schemeGenerate(
  id: string,
  body: {
    scheme: string
    node_ids?: string[]
    kind?: 'image' | 'video'
    model_id?: string
    ratio?: string
    config?: DirectorGenerateBody['config']
    shots?: DirectorGenerateBody['shots']
  },
): Promise<SchemeGenerateOut> {
  return api.post(`/canvas/${id}/scheme-generate`, body)
}

export async function findBoardForSegment(segmentId: string): Promise<string | null> {
  const boards = await listBoards()
  for (const b of boards) {
    const doc = b.document as unknown as { nodes?: Array<{ type?: string; data?: { segmentId?: string } }> }
    if (doc?.nodes?.some((n) => n.type === 'shot' && n.data?.segmentId === segmentId)) return b.id
  }
  return null
}

export interface CanvasAuditItem {
  node_id: string
  label: string
  issue: 'failed' | 'unbound' | 'no_product' | 'no_point'
  detail?: string
}

export interface CanvasAudit {
  board_id: string
  items: CanvasAuditItem[]
  running_tasks: number
  failed_tasks: number
  recent_failed: { task_id: string; error?: string }[]
}

export function auditBoard(id: string): Promise<CanvasAudit> {
  return api.get(`/canvas/${id}/audit`)
}

export function enhanceSegmentPrompt(
  segmentId: string,
  prompt?: string,
  target: 'image' | 'video' = 'image',
): Promise<{ enhanced_prompt: string; negative_prompt: string }> {
  return api.post(`/segments/${segmentId}/enhance-prompt`, { prompt: prompt ?? null, target })
}

export function listProjects(): Promise<ProjectLite[]> {
  return api.get('/projects')
}

export function listProjectSegments(projectId: string): Promise<SegmentLite[]> {
  return api.get(`/projects/${projectId}/segments`)
}
