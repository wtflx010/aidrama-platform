import type { NodeTypes } from '@xyflow/react'
import { ShotNode } from './ShotNode'
import { AssetNode } from './AssetNode'
import { StoryboardNode } from './StoryboardNode'
import { VideoNode } from './VideoNode'
import { GroupNode } from './GroupNode'
import { NoteNode } from './NoteNode'
import { DirectorSchemeNode } from './DirectorSchemeNode'
import type { CanvasNode } from '../../../lib/canvasTypes'

export const canvasNodeTypes: NodeTypes = {
  shot: ShotNode as never,
  asset: AssetNode as never,
  storyboard: StoryboardNode as never,
  video: VideoNode as never,
  group: GroupNode as never,
  note: NoteNode as never,
  // ComfyUI 式方案节点（2026-08-30）：方案控件直接长在画布节点上
  director: DirectorSchemeNode as never,
}

export type { CanvasNode }
