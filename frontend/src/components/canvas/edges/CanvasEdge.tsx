import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react'
import { EDGE_SEMANTICS, type EdgeSemantic } from '../../../lib/canvasTypes'

/** 自定义连线:按语义区分线色/虚实(衔接=绿实线,承接=蓝实线,参考=灰虚线) */
export function CanvasEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data } = props
  const sem = (data as { semantic?: EdgeSemantic } | undefined)?.semantic ?? 'sequence'
  const cfg = EDGE_SEMANTICS[sem]
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })
  return (
    <BaseEdge
      id={id}
      path={path}
      style={{
        stroke: cfg.color,
        strokeWidth: 2,
        strokeOpacity: 0.8,
        strokeDasharray: cfg.dashed ? '7 5' : undefined,
      }}
    />
  )
}
