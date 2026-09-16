/** 分镜工作台 · 中列：视频工作台（分镜概览/项目成片 页签 + 中心播放器 + 分镜/BGM/SFX 横链） */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { Episode, Project, Segment, VideoClip } from '../../api/types'
import { Badge } from '../ui/Badge'
import { EmptyState } from '../ui/EmptyState'
import { ProgressBar } from '../ui/ProgressBar'
import { Icon } from '../../lib/icons'
import { cn } from '../../lib/cn'

interface BgmItem { id: string; project_id: string | null; emotion: string; audio_url: string | null; duration: number; volume: number; status: string; source: string; prompt: string }
interface SfxItem { id: string; segment_id: string | null; sfx_type: string; sfx_name: string; audio_url: string | null; duration: number; volume: number; status: string }
interface TaskRow { id: string; status: string; progress: number; type: string }
type CenterTab = 'shot' | 'film'

export function StudioMiddleColumn({ project, selected, segments, epById, centerTab, setCenterTab, onSelect, allClipMap, projectId, showBatch, setShowBatch }: {
  project: Project
  selected: Segment | null
  segments: Segment[]
  epById: Map<string, Episode>
  centerTab: CenterTab
  setCenterTab: (t: CenterTab) => void
  onSelect: (id: string) => void
  allClipMap: Record<string, VideoClip[]>
  projectId: string
  showBatch: boolean
  setShowBatch: (v: boolean) => void
}) {
  // 取「成功」视频：优先超清版（is_upscaled），无超清版回退最新一条成功原片。
  // 2026-08-23 修复：此前取 ok[0]（列表按 created_at ASC = 最早一条），当同分镜
  // 存在多条成功成片（旧版本 regenerate 只追加不删除导致）时永远显示旧视频。
  // 这里改为取「最新一条」，与后端「每分镜只留最新一条」语义对齐。
  function pickClip(list: VideoClip[] | undefined): VideoClip | null {
    const ok = (list || []).filter((v) => v.status === 'succeeded')
    if (ok.length === 0) return null
    return ok.find((v) => v.is_upscaled) || ok[ok.length - 1]
  }
  const currentClip = selected ? pickClip(allClipMap[selected.id]) : null

  // 成片播放器：把各分镜已生成视频按分镜顺序「组合」成一个连续播放序列（跳过无视频的分镜），
  // 播放中的分镜实时驱动预览信息条与底部分镜横链高亮。
  const filmClips = useMemo(
    () =>
      segments
        .map((s) => ({ seg: s, clip: pickClip(allClipMap[s.id]) }))
        .filter((x): x is { seg: Segment; clip: VideoClip } => !!x.clip),
    [segments, allClipMap],
  )
  const [filmSegId, setFilmSegId] = useState<string | null>(null)
  const [pidx, setPidx] = useState(0)
  const cur = filmClips.length > 0 ? filmClips[Math.min(pidx, filmClips.length - 1)] : null

  useEffect(() => {
    if (centerTab === 'film') setFilmSegId(cur ? cur.seg.id : null)
  }, [cur, centerTab])

  function handleFilmEnd() {
    if (filmClips.length === 0) return
    setPidx((p) => (p + 1) % filmClips.length)
  }
  function handleFilmJump(segId: string) {
    const i = filmClips.findIndex((x) => x.seg.id === segId)
    if (i >= 0) setPidx(i)
  }
  const playingSeg = centerTab === 'film' && filmSegId
    ? segments.find((s) => s.id === filmSegId) || null
    : null
  const infoSeg = playingSeg || selected   // 播放中的分镜优先，否则用当前选中分镜
  const activeRailId = centerTab === 'film' ? (filmSegId || selected?.id) : selected?.id

  // 左下角预览信息条：分镜概览→「分镜预览 · 5s · 720P」；项目成片→「成片预览 · 1/60 · 1-1 · 标题 · 5s」
  function previewTag() {
    const dur = Math.round(infoSeg?.duration ?? 0)
    const shotTitle = infoSeg?.title || (infoSeg?.description || '').slice(0, 12) || '分镜'
    const order = infoSeg ? segments.findIndex((s) => s.id === infoSeg.id) + 1 : 0
    const total = segments.length
    // 分辨率标签取实际视频（优先超清版）的宽高；拿不到时回退项目设置分辨率
    const resText =
      currentClip && currentClip.width && currentClip.height
        ? `${currentClip.width}×${currentClip.height}${currentClip.is_upscaled ? ' · 超清' : ''}`
        : ((project.resolution || '720P').toUpperCase())
    const text =
      centerTab === 'shot'
        ? '分镜预览 · ' + dur + 's · ' + resText
        : '成片预览 · ' + order + '/' + total + ' · ' + (infoSeg ? seqOf(infoSeg, epById) : '—') + ' · ' + shotTitle + ' · ' + dur + 's'
    return (
      <div
        className="absolute bottom-3 left-3 z-10 px-2.5 py-1 rounded-full bg-black/55 text-violet-300 text-xs font-medium pointer-events-none whitespace-nowrap max-w-[75%] truncate"
        title={text}
      >
        {text}
      </div>
    )
  }

  return (
    <div className="flex-1 min-w-0 flex flex-col relative rounded-xl border border-slate-200 bg-white overflow-hidden">
      {/* 顶部操作条：左页签切换；右侧按页签显示当前内容标识（分镜标题+序号 / 项目标题） */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200">
        <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-0.5">
          {([['shot', '分镜概览'], ['film', '项目成片']] as [CenterTab, string][]).map(([k, l]) => (
            <button key={k} onClick={() => setCenterTab(k)}
              className={cn('px-3 py-1.5 text-xs font-medium rounded-md transition-colors', centerTab === k ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500 hover:text-slate-700')}>
              {l}
            </button>
          ))}
        </div>
        {centerTab === 'shot' ? (
          <div className="min-w-0 flex items-center gap-2 ml-4">
            <span className="text-[11px] font-mono text-slate-400 shrink-0">{selected ? seqOf(selected, epById) : '—'}</span>
            <span className="text-xs font-medium text-slate-700 truncate">
              {selected?.title || selected?.description?.slice(0, 16) || '分镜'}
            </span>
          </div>
        ) : (
          <div className="min-w-0 flex items-center gap-2 ml-4">
            <span className="text-xs font-medium text-slate-700 truncate">{project.title}</span>
          </div>
        )}
      </div>

      {/* 中心播放器 */}
      {/* 间距与三列之间一致（三列 gap/p 为 16px → p-4） */}
      <div className="flex-1 min-h-0 relative flex items-center justify-center p-4">
        {centerTab === 'shot' ? (
          currentClip?.video_url ? (
            <div className="relative w-full h-full flex items-center justify-center">
              <video key={currentClip.id} src={currentClip.video_url} controls autoPlay className="w-full h-full rounded-xl bg-black object-contain" />
              {previewTag()}
            </div>
          ) : (
            <EmptyState icon="video" title="该分镜暂无视频" description="点击右上角「生成所有分镜」或右侧「重新生成当前分镜」" />
          )
        ) : (
          <div className="relative w-full h-full">
            {cur ? (
              <video key={cur.clip.id} src={cur.clip.video_url || undefined} controls autoPlay
                onEnded={handleFilmEnd}
                className="w-full h-full rounded-xl bg-black object-contain" />
            ) : (
              <div className="w-full h-full flex items-center justify-center">
                <p className="text-slate-400 text-sm text-center">
                  暂无已生成的分镜视频<br/>点击右上角「生成所有分镜」后，在此按分镜时序连续播放
                </p>
              </div>
            )}
            {previewTag()}
          </div>
        )}
      </div>

      {/* 分镜横链 */}
      <div className="px-4 py-2 border-t border-slate-200">
        <div className="text-[11px] text-slate-400 mb-1.5 flex items-center gap-1"><Icon name="film" size={11} /> 分镜视频</div>
        <div className="flex gap-1 overflow-x-auto pb-1">
          {segments.map((s) => {
            const ep = epById.get(s.episode_id)
            const hasVideo = !!allClipMap[s.id]?.some((v) => v.status === 'succeeded')
            return (
              <button key={s.id} onClick={() => { onSelect(s.id); if (centerTab === 'film') handleFilmJump(s.id) }}
                className={cn('shrink-0 px-1.5 py-1.5 rounded-md border text-[11px] font-mono leading-none transition-colors',
                  activeRailId === s.id ? 'bg-brand-500 text-white border-brand-500' : 'bg-white text-slate-600 border-slate-200 hover:border-brand-400')}>
                {(ep?.index ?? 0) + 1}-{s.index}
                {hasVideo && <span className="ml-1 text-[8px] opacity-70">●</span>}
              </button>
            )
          })}
        </div>
      </div>

      {/* BGM / SFX 横链 */}
      <div className="px-4 pb-2 border-t border-slate-100">
        <BgmSfxRails />
      </div>

      {showBatch && <BatchProgress onClose={() => setShowBatch(false)} projectId={projectId} />}
    </div>
  )
}

function BgmSfxRails() {
  const { data: lib } = useQuery({
    queryKey: ['audio-library'],
    queryFn: () => api.get<{ bgm: BgmItem[]; sfx: SfxItem[] }>('/audio/library'),
  })
  const bgms = (lib?.bgm ?? []).filter((b) => b.status === 'done' || b.status === 'succeeded')
  const sfxs = (lib?.sfx ?? []).filter((s) => s.status === 'done' || s.status === 'succeeded')
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        <span className="text-[11px] text-slate-400 shrink-0 w-9"><Icon name="music" size={11} className="inline mr-0.5" />BGM</span>
        {bgms.length === 0 && <span className="text-[11px] text-slate-300 shrink-0">暂无 BGM，可去「音频」页签生成</span>}
        {bgms.map((b) => (
          <div key={b.id} className="shrink-0 flex items-center gap-1 px-1.5 py-1.5 rounded-md border border-slate-200 bg-white">
            {b.audio_url && <audio preload="none" className="h-6 w-12" src={b.audio_url} controls />}
            <Badge variant="gray" size="sm">{b.emotion}</Badge>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        <span className="text-[11px] text-slate-400 shrink-0 w-9"><Icon name="headphones" size={11} className="inline mr-0.5" />SFX</span>
        {sfxs.length === 0 && <span className="text-[11px] text-slate-300 shrink-0">暂无音效</span>}
        {sfxs.map((s) => (
          <div key={s.id} className="shrink-0 flex items-center gap-1 px-1.5 py-1.5 rounded-md border border-slate-200 bg-white">
            {s.audio_url && <audio preload="none" className="h-6 w-12" src={s.audio_url} controls />}
            <span className="text-[10px] text-slate-400">{s.sfx_name.slice(0, 6)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function BatchProgress({ onClose, projectId }: { onClose: () => void; projectId: string }) {
  const { data: tasks = [] } = useQuery({
    queryKey: ['tasks', projectId],
    queryFn: () => api.get<TaskRow[]>('/projects/' + projectId + '/tasks'),
    refetchInterval: 3000,
  })
  // 生成分镜 / 超分分镜 批量任务共用进度卡：取两类中最新的一条
  const batch = tasks.filter((t) => t.type === 'batch_videos' || t.type === 'batch_upscale_videos').slice(-1)[0]
  const upscaling = batch?.type === 'batch_upscale_videos'
  const title = upscaling ? '超分所有分镜' : '生成所有分镜'
  return (
    <div className="absolute bottom-24 right-4 w-80 rounded-xl bg-white border border-slate-200 shadow-xl p-4 z-20">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-slate-800">{title}</span>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><Icon name="x" size={14} /></button>
      </div>
      {!batch ? <p className="text-xs text-slate-400">队列中…</p> : (
        <div className="space-y-1.5">
          <ProgressBar value={batch.progress ?? 0} variant="brand" label={batch.status === 'succeeded' ? '全部完成' : batch.status === 'failed' ? '部分失败' : (upscaling ? '超分中 ' : '生成中 ') + (batch.progress ?? 0) + '%'} />
          {upscaling && batch.status === 'pending' || upscaling && batch.status === 'running' ? (
            <p className="text-[10px] text-slate-400">单镜 480p→1080p 约 2~9 分钟，整批按后台队列顺序执行</p>
          ) : null}
          {(batch.status === 'succeeded' || batch.status === 'failed') && <p className="text-xs text-slate-400">任务中心可见详情</p>}
        </div>
      )}
    </div>
  )
}
function seqOf(s: { episode_id: string; index: number }, epById: Map<string, { index: number }>): string {
  const ep = epById.get(s.episode_id)
  return (ep ? ep.index + 1 : '?').toString() + '-' + s.index.toString()
}
