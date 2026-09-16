import { useMemo } from 'react'

interface Point {
  ts: number
  value: number | null | undefined
}

interface LineChartProps {
  data: Point[]
  height?: number
  color?: string
  unit?: string
  /** 固定 y 轴最大（如百分比固定 100）；不传则按数据自适应 */
  yMax?: number
  /** 固定 y 轴最小；不传则取 0 */
  yMin?: number
  /** 最多渲染点数，超出做均匀抽稀（默认 240） */
  maxPoints?: number
}

const CHART_W = 600
const CHART_H = 120
const PAD = 6

/** 轻量级 SVG 折线图：历史趋势用，无外部依赖。 */
export function LineChart({ data, height = 120, color = '#8b5cf6', unit = '', yMax, yMin = 0, maxPoints = 240 }: LineChartProps) {
  const { path, area, minV, maxV, lastVal } = useMemo(() => {
    const pts = data.filter((p) => p.value !== null && p.value !== undefined && !isNaN(p.value as number)) as { ts: number; value: number }[]
    if (pts.length === 0) return { path: '', area: '', minV: 0, maxV: 0, lastVal: null as number | null }

    // 抽稀：点数过多时均匀采样
    let sampled = pts
    if (pts.length > maxPoints) {
      const step = pts.length / maxPoints
      sampled = pts.filter((_, i) => Math.floor(i / step) !== Math.floor((i + 1) / step) || i === pts.length - 1)
    }

    const values = sampled.map((p) => p.value)
    let lo = yMin ?? Math.min(...values)
    let hi = yMax ?? Math.max(...values)
    if (hi - lo < 1e-6) hi = lo + 1
    const spanT = sampled[sampled.length - 1].ts - sampled[0].ts || 1
    const spanV = hi - lo

    const x = (ts: number) => PAD + ((ts - sampled[0].ts) / spanT) * (CHART_W - PAD * 2)
    const y = (v: number) => CHART_H - PAD - ((v - lo) / spanV) * (CHART_H - PAD * 2)

    let d = `M ${x(sampled[0].ts)} ${y(sampled[0].value)}`
    for (let i = 1; i < sampled.length; i++) d += ` L ${x(sampled[i].ts)} ${y(sampled[i].value)}`
    const areaD = `${d} L ${x(sampled[sampled.length - 1].ts)} ${CHART_H - PAD} L ${x(sampled[0].ts)} ${CHART_H - PAD} Z`

    return { path: d, area: areaD, minV: lo, maxV: hi, lastVal: pts[pts.length - 1].value }
  }, [data, yMax, yMin, maxPoints])

  const show = path !== ''
  const vb = `0 0 ${CHART_W} ${CHART_H}`

  return (
    <div className="relative" style={{ height }}>
      <svg viewBox={vb} preserveAspectRatio="none" className="w-full h-full overflow-visible">
        {/* 网格线 */}
        <line x1={PAD} x2={CHART_W - PAD} y1={PAD} y2={PAD} stroke="#f1f5f9" strokeWidth="1" />
        <line x1={PAD} x2={CHART_W - PAD} y1={CHART_H / 2} y2={CHART_H / 2} stroke="#f1f5f9" strokeWidth="1" />
        <line x1={PAD} x2={CHART_W - PAD} y1={CHART_H - PAD} y2={CHART_H - PAD} stroke="#e2e8f0" strokeWidth="1" />
        {show && (
          <>
            <path d={area} fill={color} opacity="0.08" />
            <path d={path} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
          </>
        )}
      </svg>
      {show && (
        <>
          <span className="absolute left-0 -top-1.5 text-[10px] text-slate-400 tabular-nums">
            {unit ? `${Number(maxV.toFixed(1))}${unit}` : maxV.toFixed(1)}
          </span>
          <span className="absolute right-0 -top-1.5 text-[10px] text-slate-400 tabular-nums">
            {unit ? `${Number(minV.toFixed(1))}${unit}` : minV.toFixed(1)}
          </span>
          <span className="absolute right-0 bottom-0 text-[11px] font-medium text-slate-700 tabular-nums">
            {lastVal != null ? `${Number(lastVal.toFixed(1))}${unit}` : '--'}
          </span>
        </>
      )}
      {!show && <div className="absolute inset-0 flex items-center justify-center text-[11px] text-slate-300">暂无数据</div>}
    </div>
  )
}