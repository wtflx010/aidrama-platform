/**
 * 项目创建/删除确认卡片。
 */
import { useState } from 'react'
import { Button } from '../ui/Button'
import { Icon } from '../../lib/icons'
import { cn } from '../../lib/cn'
import { DEFAULT_GEN_RESOLUTION } from '../../lib/videoConfig'
import { StylePicker } from '../StylePicker'
import { VideoParamsEditor, type VideoParams } from '../VideoParamsEditor'
import { ASPECT_RATIOS } from './shared'
import type { ConfirmProjectOpts, LocalMsg, ProjectDraftPayload, ProjectDeletePayload } from './shared'
import type { AspectRatio } from '../../api/types'

export function ProjectDraftCard({
  msg,
  payload,
  onConfirm,
}: {
  msg: LocalMsg
  payload: ProjectDraftPayload
  onConfirm?: (msg: LocalMsg, opts: ConfirmProjectOpts) => void
}) {
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>(payload.default.aspect_ratio)
  const [style, setStyle] = useState<{ style_id: string | null; art_style_prompt: string | null }>({
    style_id: payload.default.style_id,
    art_style_prompt: payload.default.art_style_prompt,
  })
  const [videoParams, setVideoParams] = useState<VideoParams>({})
  const [confirming, setConfirming] = useState(false)
  const draft = payload.draft ?? {}
  const assets = draft.assets ?? []
  const eps = draft.episodes ?? []
  const chars = assets.filter((a) => a.type === 'character').length
  const scenes = assets.filter((a) => a.type === 'scene').length
  const props = assets.filter((a) => a.type === 'prop').length
  const segs = eps.reduce((n, ep) => n + (ep.segments?.length ?? 0), 0)

  const handleConfirm = async () => {
    setConfirming(true)
    try {
      await onConfirm?.(msg, {
        aspect_ratio: aspectRatio,
        style_id: style.style_id,
        art_style_prompt: style.art_style_prompt,
        resolution: DEFAULT_GEN_RESOLUTION,
        video_params: videoParams as Record<string, unknown>,
      })
    } finally {
      setConfirming(false)
    }
  }

  const stat = (icon: string, label: string, count: number) => (
    <span className="flex items-center gap-1.5 text-slate-600">
      <Icon name={icon} size={12} className="text-slate-400" />
      {label} {count}
    </span>
  )

  return (
    <div className="max-w-[520px] w-full">
      <div className="rounded-2xl border border-brand-200 bg-white overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-brand-100 bg-brand-50/60">
          <Icon name="folder" size={14} className="text-brand-500" />
          <span className="text-xs font-semibold text-brand-700 truncate">{payload.title || draft.title || '新项目'}</span>
          <span className="text-[10px] font-medium text-brand-600 bg-white rounded-full px-2 py-0.5 ml-auto shrink-0">待确认</span>
        </div>
        <div className="p-3 space-y-3">
          {/* 内容预览 */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px]">
            {stat('film', '幕', eps.length)}
            {stat('users', '角色', chars)}
            {stat('map-pin', '场景', scenes)}
            {stat('package', '道具', props)}
            {stat('clapperboard', '分镜', segs)}
          </div>
          {draft.synopsis && (
            <p className="text-xs text-slate-500 leading-relaxed line-clamp-2">{draft.synopsis}</p>
          )}
          {/* 画面尺寸 */}
          <div>
            <label className="text-xs text-slate-400 block mb-1.5">画面尺寸</label>
            <div className="flex items-center gap-2 flex-wrap">
              {ASPECT_RATIOS.map((ar) => (
                <button
                  key={ar.value}
                  type="button"
                  onClick={() => setAspectRatio(ar.value)}
                  className={cn(
                    'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-all',
                    aspectRatio === ar.value
                      ? 'border-brand-500 bg-brand-50 text-brand-600'
                      : 'border-slate-200 text-slate-500 hover:border-brand-300 hover:text-slate-700',
                  )}
                >
                  <span className={cn('border-2 rounded-[2px]', ar.box, aspectRatio === ar.value ? 'border-brand-500' : 'border-slate-300')} />
                  {ar.label}
                </button>
              ))}
            </div>
          </div>
          {/* 美术风格 */}
          <div>
            <label className="text-xs text-slate-400 block mb-1.5">美术风格</label>
            <StylePicker value={style} onChange={setStyle} compact />
          </div>
          {/* 项目级视频参数（创建时设定，项目详情可再改；批量生成按此出片） */}
          <div className="rounded-xl border border-slate-200 p-2.5">
            <VideoParamsEditor value={videoParams} onChange={setVideoParams} title="视频参数（项目级）" />
          </div>
          <Button
            size="sm"
            className="w-full"
            loading={confirming}
            onClick={handleConfirm}
            leftIcon={<Icon name="check" size={12} />}
          >
            确认创建项目
          </Button>
          <p className="text-[11px] text-slate-400 flex items-center gap-1">
            <Icon name="info" size={11} className="text-brand-500" />
            确认后将按以上内容创建项目：角色/场景/道具资产与分镜全部入库，可进入项目工作台查看
          </p>
        </div>
      </div>
    </div>
  )
}

/** 项目删除确认卡：展示项目名+内容统计，用户确认后真正删除（含资产/分镜/视频及磁盘文件） */
export function ProjectDeleteCard({
  msg,
  payload,
  onConfirm,
}: {
  msg: LocalMsg
  payload: ProjectDeletePayload
  onConfirm?: (msg: LocalMsg) => void
}) {
  const [deleting, setDeleting] = useState(false)

  const handleConfirm = async () => {
    setDeleting(true)
    try {
      await onConfirm?.(msg)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="max-w-[460px] w-full">
      <div className="rounded-2xl border border-rose-200 bg-white overflow-hidden shadow-sm">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-rose-100 bg-rose-50/60">
          <Icon name="trash" size={14} className="text-rose-500" />
          <span className="text-xs font-semibold text-rose-700 truncate">{payload.title || '删除项目'}</span>
          <span className="text-[10px] font-medium text-rose-600 bg-white rounded-full px-2 py-0.5 ml-auto shrink-0">待确认删除</span>
        </div>
        <div className="p-3 space-y-3">
          <div className="flex items-start gap-2 text-xs text-slate-600 leading-relaxed">
            <Icon name="alert-circle" size={14} className="text-amber-500 shrink-0 mt-0.5" />
            <p>
              确认删除项目「<span className="font-medium text-slate-800">{payload.title}</span>」？{payload.detail && `该项目包含 ${payload.detail}，`}
              删除后不可恢复（幕/分镜/资产/视频/音频及磁盘文件一并清除）。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="danger"
              className="flex-1"
              loading={deleting}
              onClick={handleConfirm}
              leftIcon={<Icon name="trash" size={12} />}
            >
              确认删除
            </Button>
          </div>
          <p className="text-[11px] text-slate-400 flex items-center gap-1">
            <Icon name="info" size={11} className="text-rose-400" />
            删除操作不可撤销，请确认项目名称无误
          </p>
        </div>
      </div>
    </div>
  )
}


