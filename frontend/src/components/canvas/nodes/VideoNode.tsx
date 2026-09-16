import { useEffect, useRef, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import { cn } from '../../../lib/cn'
import { videoPlaceholder, hueFrom } from '../../../lib/canvasImages'
import type { CanvasNode } from '../../../lib/canvasTypes'
import { NodeHandles } from './NodeHandles'

/** 视频节点:就地预览产物(原型:模拟播放进度) */
export function VideoNode({ data, selected }: NodeProps<CanvasNode>) {
  const d = data
  const [playing, setPlaying] = useState(false)
  const [sec, setSec] = useState(0)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const total = d.durationSec ?? 0

  useEffect(() => {
    if (playing) {
      timer.current = setInterval(() => setSec((s) => (s + 1 >= total ? 0 : s + 1)), 1000)
    } else if (timer.current) {
      clearInterval(timer.current)
      timer.current = null
    }
    return () => {
      if (timer.current) clearInterval(timer.current)
    }
  }, [playing, total])

  const thumb =
    d.imageUrl ??
    videoPlaceholder({
      w: 640,
      h: 360,
      hue: hueFrom(d.label ?? 'video'),
      label: d.label ?? '视频',
      duration: `${total}s`,
    })

  return (
    <div
      className={cn(
        'w-[250px] overflow-hidden rounded-xl border bg-slate-900 shadow-sm transition-shadow',
        selected ? 'border-slate-400 ring-2 ring-brand-500/70 shadow-md' : 'border-slate-700 hover:shadow-md',
      )}
    >
      <NodeHandles />
      <div className="relative">
        {d.videoUrl ? (
          <video src={d.videoUrl} controls playsInline className="h-[140px] w-full bg-black object-contain" />
        ) : (
          <>
            <img src={thumb} alt={d.label ?? ''} className="h-[140px] w-full object-cover" />
            <button
              onClick={(e) => {
                e.stopPropagation()
                setPlaying((p) => !p)
              }}
              className="absolute inset-0 grid place-items-center bg-black/20 text-white hover:bg-black/30"
            >
              <span className="grid h-10 w-10 place-items-center rounded-full bg-white/90 text-slate-900 shadow">
                {playing ? '❚❚' : '▶'}
              </span>
            </button>
            <span className="absolute bottom-1.5 right-1.5 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[10px] text-white">
              {Math.floor(sec / 60)}:{String(sec % 60).padStart(2, '0')} / {total}s
            </span>
          </>
        )}
      </div>
      <div className="px-2.5 py-2">
        <p className="text-[11px] font-medium text-white">{d.label ?? '视频'}</p>
        <p className="text-[10px] text-slate-400">{d.description ?? ''}</p>
      </div>
    </div>
  )
}
