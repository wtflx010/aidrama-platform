import type { ImageVersion } from '../../../lib/canvasTypes'
import { cn } from '../../../lib/cn'

/** 节点版本栈:生成结果的版本堆叠,点选任意版本替换画布显示 */
export function VersionStack({
  versions,
  active,
  onSelect,
}: {
  versions: ImageVersion[]
  active: number
  onSelect: (i: number) => void
}) {
  if (versions.length === 0) {
    return <p className="text-[11px] text-slate-400">暂无版本,点击「开始生成」创建第一个版本。</p>
  }
  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1">
      {versions.map((v, i) => (
        <button
          key={v.id}
          type="button"
          title={`v${i + 1} ${v.createdAt}`}
          onClick={() => onSelect(i)}
          className={cn(
            'relative h-12 w-20 shrink-0 overflow-hidden rounded-md border transition-all',
            i === active ? 'border-brand-500 ring-1 ring-brand-500' : 'border-slate-200 opacity-70 hover:opacity-100',
          )}
        >
          <img src={v.url} alt={v.note ?? `v${i + 1}`} className="h-full w-full object-cover" />
          <span className="absolute bottom-0 right-0.5 text-[9px] font-semibold text-white drop-shadow">v{i + 1}</span>
        </button>
      ))}
    </div>
  )
}
