import { COMPOSITION_POINTS, type CompositionPoint } from '../../../lib/canvasTypes'
import { cn } from '../../../lib/cn'

const ORDER: CompositionPoint[] = [
  'top_left', 'top', 'top_right',
  'left', 'center', 'right',
  'bottom_left', 'bottom', 'bottom_right',
]

/** 九宫格构图点位选择器(与 P5 composition_point 九区对齐) */
export function NineGridPicker({
  value,
  onChange,
}: {
  value?: CompositionPoint
  onChange: (v: CompositionPoint) => void
}) {
  return (
    <div className="grid grid-cols-3 gap-1">
      {ORDER.map((p) => (
        <button
          key={p}
          type="button"
          title={COMPOSITION_POINTS[p]}
          onClick={() => onChange(p)}
          className={cn(
            'h-6 rounded border text-[9px] transition-colors',
            value === p
              ? 'border-brand-500 bg-brand-500/10 font-semibold text-brand-600'
              : 'border-slate-200 text-slate-400 hover:border-slate-300 hover:text-slate-500',
          )}
        >
          {COMPOSITION_POINTS[p]}
        </button>
      ))}
    </div>
  )
}
