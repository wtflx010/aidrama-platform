import type { Node, Edge } from '@xyflow/react'

/** 九宫格构图点位(与后端 P5 segment.composition_point 对齐) */
export const COMPOSITION_POINTS = {
  top_left: '左上',
  top: '上中',
  top_right: '右上',
  left: '左中',
  center: '正中',
  right: '右中',
  bottom_left: '左下',
  bottom: '下中',
  bottom_right: '右下',
} as const
export type CompositionPoint = keyof typeof COMPOSITION_POINTS

export interface CameraParams {
  speed: 'slow' | 'medium' | 'fast'
  angle: 'front' | 'side' | 'low' | 'high' | 'top'
  intensity: 'subtle' | 'normal' | 'strong'
}

export interface ImageVersion {
  id: string
  url: string
  createdAt: string
  note?: string
}

export type TaskStatus = 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'error'

/** M0 原型阶段节点数据(与方案文档 §4.2 对齐;后续接后端 JSONB document) */
export interface CanvasNodeData {
  label?: string
  kind?: 'character' | 'scene' | 'prop'
  description?: string
  prompt?: string
  imageUrl?: string
  versions?: ImageVersion[]
  activeVersion?: number
  compositionPoint?: CompositionPoint
  cameraParams?: CameraParams
  lightingContinuity?: string
  segmentId?: string
  assetId?: string
  durationSec?: number
  status?: TaskStatus
  /** 生成失败错误信息(worker 回写) */
  error?: string
  /** 视频生成失败信息(worker 回写,不覆盖关键帧状态) */
  videoError?: string
  /** 画布视频产物直链(worker 成功回写) */
  videoUrl?: string
  /** 生成该视频的后端子任务 id */
  videoTaskId?: string
  /** 导演台模式产物/任务标记（2026-08-29） */
  directorTaskId?: string
  /** ComfyUI 式方案节点（2026-08-30）:控件长在画布节点上 */
  schemeKey?: string
  schemeLabel?: string
  shots?: Array<{ node_id: string; label?: string; prompt?: string; duration_sec?: number; from_prev?: boolean }>
  taskType?: string
  ratio?: string
  res?: string
  contextEnabled?: boolean
  contextFrames?: number
  previewUrl?: string
  /** 归属项目(分镜导入节点带上,用于跳转剪辑器) */
  projectId?: string
  /** 分镜镜头类型(小全/中景/特写…) */
  shotType?: string
  progress?: number
  noteText?: string
  memberCount?: number
  /** 满足 xyflow Record<string, unknown> 数据约束的兜底索引 */
  [key: string]: unknown
}

export type CanvasNode = Node<CanvasNodeData>
export type CanvasEdge = Edge<{ semantic: 'reference' | 'continuation' | 'sequence'; label?: string }>

export const EDGE_SEMANTICS = {
  sequence: { label: '衔接', color: '#10b981', dashed: false },
  continuation: { label: '承接', color: '#3b82f6', dashed: false },
  reference: { label: '参考', color: '#94a3b8', dashed: true },
} as const
export type EdgeSemantic = keyof typeof EDGE_SEMANTICS

export const MOCK_MODELS = [
  { id: 'agnes-image-2.1-flash', kind: 'image' as const, label: 'Agnes 文生图 2.1-flash' },
  { id: 'agnes-image-2.0-flash', kind: 'image' as const, label: 'Agnes 图生图 2.0-flash' },
  { id: 'agnes-video-v2.0', kind: 'video' as const, label: 'Agnes 视频 v2.0(首尾帧)' },
]
