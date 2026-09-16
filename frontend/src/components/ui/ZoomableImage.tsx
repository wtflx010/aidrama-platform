/**
 * 双击放大图片组件：包裹任意 <img>，双击打开全屏预览。
 *
 * Lightbox 能力：
 * - 滚轮缩放（1x ~ 8x，居中缩放）
 * - 按住拖拽平移
 * - 双击图片还原 100%；点击黑色背景 / 关闭按钮 / ESC 关闭
 */
import { useEffect, useRef, useState } from 'react'
import { cn } from '../../lib/cn'
import { Icon } from '../../lib/icons'

export function ZoomableImage({
  src,
  alt,
  className,
  imgClassName,
}: {
  src: string
  alt?: string
  className?: string
  /** 附加到内部 <img> 的类名（如加载中样式） */
  imgClassName?: string
}) {
  const [open, setOpen] = useState(false)
  if (!src) return null

  return (
    <>
      <img
        src={src}
        alt={alt}
        className={cn(className, imgClassName, 'cursor-zoom-in')}
        onDoubleClick={() => setOpen(true)}
        title={alt ? `${alt}（双击放大）` : '双击放大'}
        draggable={false}
      />
      {open && <ImageLightbox src={src} alt={alt} onClose={() => setOpen(false)} />}
    </>
  )
}

function ImageLightbox({
  src,
  alt,
  onClose,
}: {
  src: string
  alt?: string
  onClose: () => void
}) {
  const [scale, setScale] = useState(1)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  // 拖拽起始快照：鼠标起点 + 图片当时位移
  const dragRef = useRef<{ sx: number; sy: number; px: number; py: number } | null>(null)

  // ESC 关闭 + 非 passive 滚轮缩放（阻止页面滚动）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
      setScale((s) => Math.min(8, Math.max(1, s * factor)))
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('wheel', onWheel, { passive: false })
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('wheel', onWheel)
      document.body.style.overflow = prev
    }
  }, [onClose])

  function startDrag(e: React.MouseEvent) {
    dragRef.current = { sx: e.clientX, sy: e.clientY, px: pos.x, py: pos.y }
  }
  function onDrag(e: React.MouseEvent) {
    const d = dragRef.current
    if (!d) return
    setPos({ x: d.px + (e.clientX - d.sx), y: d.py + (e.clientY - d.sy) })
  }
  function endDrag() {
    dragRef.current = null
  }
  function reset() {
    setScale(1)
    setPos({ x: 0, y: 0 })
  }

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/85 flex items-center justify-center"
      onMouseDown={onClose}
    >
      {/* 图片区：阻止冒泡，支持拖拽/双击还原 */}
      <div className="relative select-none" onMouseDown={(e) => e.stopPropagation()}>
        <img
          src={src}
          alt={alt}
          draggable={false}
          onDoubleClick={(e) => {
            e.stopPropagation()
            reset()
          }}
          onMouseDown={startDrag}
          onMouseMove={onDrag}
          onMouseUp={endDrag}
          onMouseLeave={endDrag}
          className="max-w-[92vw] max-h-[88vh] object-contain shadow-2xl rounded-sm"
          style={{
            transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})`,
            transformOrigin: 'center',
            cursor: scale > 1 ? 'grabbing' : 'grab',
          }}
        />
      </div>

      {/* 顶部控制栏 */}
      <div className="absolute top-4 right-4 flex items-center gap-1.5 z-10">
        <LbButton onClick={() => setScale((s) => Math.max(1, s / 1.25))} title="缩小">
          <Icon name="minus" size={14} />
        </LbButton>
        <span className="text-xs text-white/80 w-12 text-center tabular-nums">
          {Math.round(scale * 100)}%
        </span>
        <LbButton onClick={() => setScale((s) => Math.min(8, s * 1.25))} title="放大">
          <Icon name="plus" size={14} />
        </LbButton>
        <LbButton onClick={reset} title="还原 100%">
          <Icon name="refresh" size={14} />
        </LbButton>
        <div className="w-px h-5 bg-white/20 mx-1" />
        <LbButton onClick={onClose} title="关闭 (ESC)">
          <Icon name="x" size={14} />
        </LbButton>
      </div>

      {/* 底部提示 */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 text-xs text-white/50 z-10">
        双击还原 100% · 滚轮缩放 · 拖拽平移 · ESC 关闭
      </div>
    </div>
  )
}

function LbButton({
  children,
  onClick,
  title,
}: {
  children: React.ReactNode
  onClick: () => void
  title?: string
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      className="w-8 h-8 rounded-lg bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors"
    >
      {children}
    </button>
  )
}
