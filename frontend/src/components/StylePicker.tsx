/**
 * P4 美术风格选择器：预设风格网格 + 自定义文本输入。
 * 用于项目创建弹窗与项目设置页。
 *
 * 选择逻辑（与后端 get_effective_style_prompt 一致）：
 * - 选中预设风格 → style_id = id, art_style_prompt = null
 * - 选"自定义" → style_id = null, art_style_prompt = 文本
 * - 选"不指定" → 两者皆 null
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ArtStyle, ProjectStyleSelection } from '../api/types'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'
import { ZoomableImage } from './ui/ZoomableImage'

interface StylePickerProps {
  value: ProjectStyleSelection
  onChange: (v: ProjectStyleSelection) => void
  /** 紧凑模式（创建弹窗用），默认 false（设置页用） */
  compact?: boolean
}

type Mode = 'none' | 'preset' | 'custom'

// 图片缓存破坏：cover_url 固定不变，重新生成后浏览器可能命中旧缓存。
// 追加 updated_at 时间戳作为查询参数，确保每次重新生成后展示最新图（与 AssetPanel 一致）。
function cacheBust(url: string, updatedAt: string): string {
  if (!url) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}t=${encodeURIComponent(updatedAt)}`
}

function deriveMode(v: ProjectStyleSelection): Mode {
  if (v.style_id) return 'preset'
  if (v.art_style_prompt && v.art_style_prompt.trim()) return 'custom'
  return 'none'
}

export function StylePicker({ value, onChange, compact = false }: StylePickerProps) {
  const [category, setCategory] = useState<string>('全部')
  const { data: styles = [], isLoading } = useQuery({
    queryKey: ['art-styles'],
    queryFn: () => api.get<ArtStyle[]>('/art-styles'),
  })

  const mode = deriveMode(value)
  const categories = ['全部', ...Array.from(new Set(styles.map((s) => s.category)))]
  const filtered = category === '全部' ? styles : styles.filter((s) => s.category === category)

  function selectPreset(s: ArtStyle) {
    onChange({ style_id: s.id, art_style_prompt: null })
  }
  function selectNone() {
    onChange({ style_id: null, art_style_prompt: null })
  }
  function selectCustom() {
    onChange({ style_id: null, art_style_prompt: value.art_style_prompt ?? '' })
  }
  function setCustomText(text: string) {
    onChange({ style_id: null, art_style_prompt: text })
  }

  return (
    <div className="space-y-3">
      {/* 模式切换 */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <ModeChip
          active={mode === 'none'}
          onClick={selectNone}
          icon="x"
          label="不指定"
        />
        <ModeChip
          active={mode === 'custom'}
          onClick={selectCustom}
          icon="pencil"
          label="自定义"
        />
      </div>

      {/* 预设风格网格 */}
      {mode !== 'custom' && (
        <>
          {categories.length > 1 && (
            <div className="flex items-center gap-1 flex-wrap">
              {categories.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setCategory(c)}
                  className={cn(
                    'px-2.5 py-1 rounded-full text-xs font-medium transition-all',
                    category === c
                      ? 'bg-brand-500/15 text-brand-600'
                      : 'text-slate-400 hover:text-slate-600 hover:bg-slate-100',
                  )}
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          {isLoading ? (
            <div className={cn('grid gap-2', compact ? 'grid-cols-3' : 'grid-cols-4 sm:grid-cols-5')}>
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="skeleton aspect-[4/3] rounded-lg" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-xs text-slate-400 py-4 text-center">暂无预设风格</p>
          ) : (
            <div className={cn('grid gap-2', compact ? 'grid-cols-3' : 'grid-cols-4 sm:grid-cols-5')}>
              {filtered.map((s) => {
                const selected = value.style_id === s.id
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => selectPreset(s)}
                    title={s.description || s.prompt_fragment}
                    className={cn(
                      'group relative rounded-lg border overflow-hidden transition-all text-left',
                      selected
                        ? 'border-brand-500 ring-2 ring-brand-500/30'
                        : 'border-slate-200 hover:border-brand-300',
                    )}
                  >
                    <div className="aspect-[4/3] bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center overflow-hidden">
                      {s.cover_url ? (
                        <ZoomableImage
                          src={cacheBust(s.cover_url, s.updated_at)}
                          alt={s.name}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <Icon name="image" size={20} className="text-slate-400" />
                      )}
                    </div>
                    <div className="px-2 py-1.5">
                      <p className="text-xs font-medium text-slate-800 truncate">{s.name}</p>
                      <p className="text-[10px] text-slate-400 truncate">{s.category}</p>
                    </div>
                    {selected && (
                      <span className="absolute top-1 right-1 w-5 h-5 rounded-full bg-brand-500 text-white flex items-center justify-center shadow">
                        <Icon name="check" size={12} />
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </>
      )}

      {/* 自定义文本输入 */}
      {mode === 'custom' && (
        <div className="space-y-1.5">
          <label className="text-xs text-slate-400 block">
            自定义风格描述（中英文均可，将注入到画面生成 prompt）
          </label>
          <textarea
            value={value.art_style_prompt ?? ''}
            onChange={(e) => setCustomText(e.target.value)}
            placeholder="例如：水彩风格，柔和色调，手绘质感，吉卜力工作室风格"
            rows={3}
            className="input-base resize-none"
          />
          <p className="text-[11px] text-slate-400 flex items-center gap-1">
            <Icon name="info" size={11} className="text-brand-500" />
            提示：style_id 为空时使用此文本作为风格 prompt
          </p>
        </div>
      )}
    </div>
  )
}

function ModeChip({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean
  onClick: () => void
  icon: string
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all border',
        active
          ? 'bg-gradient-brand text-white border-transparent shadow-glow-sm'
          : 'bg-white text-slate-500 border-slate-200 hover:text-slate-800',
      )}
    >
      <Icon name={icon} size={12} />
      {label}
    </button>
  )
}
