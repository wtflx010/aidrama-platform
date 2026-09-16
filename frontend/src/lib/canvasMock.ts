import type { Viewport } from '@xyflow/react'
import { versionImageUrl } from './canvasImages'
import type { CanvasEdge, CanvasNode } from './canvasTypes'

export const MOCK_VIEWPORT: Viewport = { x: 90, y: 20, zoom: 0.82 }

function shotNode(
  id: string,
  label: string,
  x: number,
  y: number,
  opts: {
    prompt: string
    point: 'center' | 'left' | 'right' | 'top' | 'bottom' | 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
    speed: 'slow' | 'medium' | 'fast'
    angle: 'front' | 'side' | 'low' | 'high' | 'top'
    intensity: 'subtle' | 'normal' | 'strong'
    lighting: string
    versions?: number
  },
): CanvasNode {
  const vCount = opts.versions ?? 1
  const versions = Array.from({ length: vCount }, (_, i) => ({
    id: `${id}-v${i}`,
    url: versionImageUrl(id, i, label),
    createdAt: '2026-08-14 12:30',
  }))
  return {
    id,
    type: 'shot',
    position: { x, y },
    data: {
      label,
      prompt: opts.prompt,
      compositionPoint: opts.point,
      cameraParams: { speed: opts.speed, angle: opts.angle, intensity: opts.intensity },
      lightingContinuity: opts.lighting,
      segmentId: `seg-${id}`,
      versions,
      activeVersion: vCount - 1,
      status: 'done',
    },
  }
}

export function buildMockNodes(): CanvasNode[] {
  return [
    {
      id: 'asset-linxiao',
      type: 'asset',
      position: { x: -660, y: 30 },
      data: {
        label: '林晓',
        kind: 'character',
        description: '女主,雨夜撑伞',
        prompt: '现代都市女青年,25岁,长发,米色风衣,剧中主视觉',
        versions: [{ id: 'a1', url: versionImageUrl('asset-linxiao', 0, '林晓·四视图'), createdAt: '2026-08-14 11:00' }],
        activeVersion: 0,
        assetId: 'asset-linxiao',
        status: 'done',
      },
    },
    {
      id: 'asset-alley',
      type: 'asset',
      position: { x: -660, y: 300 },
      data: {
        label: '雨夜巷',
        kind: 'scene',
        description: '主场景:老街巷口',
        prompt: '雨夜老街,青石板反光,暖黄路灯与冷蓝夜色的对比',
        versions: [{ id: 'a2', url: versionImageUrl('asset-alley', 0, '雨夜巷·场景'), createdAt: '2026-08-14 11:10' }],
        activeVersion: 0,
        assetId: 'asset-alley',
        status: 'done',
      },
    },
    shotNode('s01', 'S01 全景定调', -120, -10, {
      prompt: '全景夜戏,雨幕,巷口灯影;摄影机缓慢推进;主体:林晓撑伞站立;光:冷蓝夜色,主光左上45°',
      point: 'center',
      speed: 'slow',
      angle: 'front',
      intensity: 'subtle',
      lighting: '冷蓝夜戏基调,主光左上45°(幕首自定)',
      versions: 1,
    }),
    shotNode('s02', 'S02 中景对话', 240, -10, {
      prompt: '中景,两人巷中相对;镜头平视侧移;构图主体落左区;光色承接冷蓝,雨反光增强',
      point: 'left',
      speed: 'medium',
      angle: 'side',
      intensity: 'normal',
      lighting: '延续冷蓝,雨反光增强',
      versions: 2,
    }),
    shotNode('s03', 'S03 近景情绪', 600, -10, {
      prompt: '近景,女主抬眸回望;镜头缓推;构图主体右区;轮廓光勾边',
      point: 'right',
      speed: 'medium',
      angle: 'high',
      intensity: 'normal',
      lighting: '延续冷蓝,轮廓光',
      versions: 1,
    }),
    shotNode('s04', 'S04 特写收束', 240, 340, {
      prompt: '特写,雨中伞沿水珠滑落;镜头快推定向;构图正中;顶光收束',
      point: 'center',
      speed: 'fast',
      angle: 'low',
      intensity: 'strong',
      lighting: '延续冷蓝,顶光收束',
      versions: 1,
    }),
    {
      id: 'story-1',
      type: 'storyboard',
      position: { x: 620, y: 480 },
      data: {
        label: '节拍卡',
        description: '雨夜偶遇:林晓独自撑伞,在巷口与前来找她的少年相遇。动作节拍:驻足→对视→伞沿低垂。',
        prompt: '',
        status: 'idle',
      },
    },
    {
      id: 'video-1',
      type: 'video',
      position: { x: 980, y: 40 },
      data: {
        label: '粗剪预览 v1',
        description: 'S01~S04 序列拼接(演示)',
        durationSec: 20,
        status: 'done',
      },
    },
    {
      id: 'group-act1',
      type: 'group',
      position: { x: -200, y: -150 },
      data: {
        label: '第一幕 · 雨夜偶遇',
        memberCount: 4,
        status: 'idle',
      },
    },
    {
      id: 'note-1',
      type: 'note',
      position: { x: 980, y: 300 },
      data: {
        label: '便签',
        noteText: '待办:S02 需要 2 个关键帧变体;S04 试 8s 长镜',
        status: 'idle',
      },
    },
  ]
}

export function buildMockEdges(): CanvasEdge[] {
  return [
    { id: 'e-seq-1', source: 's01', target: 's02', type: 'canvas', data: { semantic: 'sequence', label: '衔接' } },
    { id: 'e-seq-2', source: 's02', target: 's03', type: 'canvas', data: { semantic: 'sequence', label: '衔接' } },
    { id: 'e-seq-3', source: 's03', target: 's04', type: 'canvas', data: { semantic: 'sequence', label: '衔接' } },
    { id: 'e-con-1', source: 's01', target: 's02', type: 'canvas', data: { semantic: 'continuation', label: '承接' } },
    { id: 'e-con-3', source: 's03', target: 's04', type: 'canvas', data: { semantic: 'continuation', label: '承接' } },
    { id: 'e-ref-1', source: 'asset-alley', target: 's01', type: 'canvas', data: { semantic: 'reference', label: '参考' } },
    { id: 'e-ref-2', source: 'asset-linxiao', target: 's02', type: 'canvas', data: { semantic: 'reference', label: '参考' } },
    { id: 'e-ref-3', source: 'asset-linxiao', target: 's03', type: 'canvas', data: { semantic: 'reference', label: '参考' } },
  ]
}

export const STORAGE_KEY = 'canvas-m0-demo-v1'
