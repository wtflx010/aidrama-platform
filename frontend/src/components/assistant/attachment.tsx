import {Icon} from '../../lib/icons'
import type {AgentAttachment} from '../../api/types'
import {ATTACH_KIND_LABELS, ATTACH_KIND_ICONS} from './shared'
export function AttachmentPreview({ att, onRemove }: { att: AgentAttachment; onRemove?: () => void }) {
  const label = ATTACH_KIND_LABELS[att.kind] ?? att.kind
  // 有图可看（图片本身 / 视频第一帧）→ 缩略图卡片
  const thumb = att.kind === 'image' ? att.url : att.kind === 'video' ? att.frames?.[0] : undefined
  if (thumb) {
    return (
      <div className="relative w-28 rounded-lg overflow-hidden border border-slate-200 bg-white group">
        <img src={thumb} alt={att.name} className="w-full h-16 object-cover" />
        <div className="px-1.5 py-1 flex items-center gap-1">
          <Icon name={ATTACH_KIND_ICONS[att.kind]} size={10} className="text-brand-500 shrink-0" />
          <span className="text-[10px] text-slate-600 truncate">{att.name}</span>
        </div>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="absolute top-0 right-0 p-0.5 bg-black/55 text-white rounded-bl-md hover:bg-black/70"
            title="移除"
          >
            <Icon name="x" size={10} />
          </button>
        )}
      </div>
    )
  }
  // 音频/文档：图标 + 名称 + 内容摘要（转写片段 / 文本开头）
  const summary =
    att.kind === 'audio'
      ? (att.transcript ?? '').replace(/^（.+）$/, '（转写中）')
      : att.kind === 'document'
        ? att.text ?? ''
        : ''
  const clip = summary.length > 40 ? summary.slice(0, 40) + '…' : summary
  return (
    <div className="relative max-w-[240px] rounded-lg border border-slate-200 bg-white px-2 py-1.5 flex items-start gap-1.5 group">
      <Icon name={ATTACH_KIND_ICONS[att.kind] ?? 'file-text'} size={13} className="text-brand-500 shrink-0 mt-0.5" />
      <span className="min-w-0">
        <span className="block text-[10px] font-medium text-slate-700 truncate">
          {label} · {att.name}
        </span>
        {clip && (
          <span className="block text-[10px] text-slate-400 leading-snug line-clamp-2">{clip}</span>
        )}
      </span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="shrink-0 p-0.5 rounded text-slate-400 hover:text-rose-500 hover:bg-slate-100"
          title="移除"
        >
          <Icon name="x" size={10} />
        </button>
      )}
    </div>
  )
}
