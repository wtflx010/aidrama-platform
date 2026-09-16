/**
 * 修片面版（P1-4）：reframe 画幅重切 + voice-change 换声。
 * 选择一幕 → 自动拉取其已生成视频片段作为源视频（可再手动改 URL）。
 * draw-to-video 需草图重绘模型，未配置暂不提供入口。
 */
import { useEffect, useState } from 'react'
import { uploadImage } from '../../api/client'
import { ASPECT_RATIOS, RESOLUTIONS, episodeVideosApi, reworkApi } from '../../api/rework'
import { useTaskPoller } from '../../api/useTaskPoller'
import type { Episode, EpisodeVideo } from '../../api/types'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { Badge } from '../ui/Badge'
import { Spinner } from '../ui/Spinner'
import { useToast } from '../ui/Toast'
import { Icon } from '../../lib/icons'

interface Props {
  open: boolean
  onClose: () => void
  episodes: Episode[]
}

export function ReworkPanel({ open, onClose, episodes }: Props) {
  const toast = useToast()
  const [episodeId, setEpisodeId] = useState('')
  const [videos, setVideos] = useState<EpisodeVideo[]>([])
  const [loadingVideos, setLoadingVideos] = useState(false)
  const [videoUrl, setVideoUrl] = useState('')
  const [ratio, setRatio] = useState('9:16')
  const [res, setRes] = useState('720p')
  const [voiceText, setVoiceText] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [emotion, setEmotion] = useState('')
  const [reframeTaskId, setReframeTaskId] = useState<string | null>(null)
  const [vcTaskId, setVcTaskId] = useState<string | null>(null)
  const [sketchUrl, setSketchUrl] = useState('')
  const [drawPrompt, setDrawPrompt] = useState('')
  const [drawRatio, setDrawRatio] = useState('16:9')
  const [drawTaskId, setDrawTaskId] = useState<string | null>(null)

  const reframeTask = useTaskPoller(reframeTaskId)
  const vcTask = useTaskPoller(vcTaskId)
  const drawTask = useTaskPoller(drawTaskId)

  // 打开时默认选上一幕并自动取成片
  useEffect(() => {
    if (open && !episodeId && episodes.length > 0) setEpisodeId(episodes[0].id)
  }, [open, episodeId, episodes])

  async function loadVideos(eid: string) {
    if (!eid) return
    setLoadingVideos(true)
    try {
      const rows = await episodeVideosApi.getByEpisode(eid)
      const withUrl = rows.filter((r) => !!r.video_url)
      setVideos(withUrl)
      if (withUrl.length > 0) setVideoUrl(withUrl[0].video_url ?? '')
    } catch (e) {
      setVideos([])
      toast.error((e as Error).message)
    } finally {
      setLoadingVideos(false)
    }
  }

  useEffect(() => {
    if (open && episodeId) loadVideos(episodeId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episodeId, open])

  useEffect(() => {
    if (!reframeTask || !reframeTaskId) return
    if (reframeTask.status === 'succeeded' || reframeTask.status === 'failed' || reframeTask.status === 'cancelled') {
      if (reframeTask.status === 'succeeded') toast.success('画幅重切完成，见下方结果')
      else if (reframeTask.status === 'failed') toast.error('画幅重切失败：' + (reframeTask.error || ''))
      setReframeTaskId(null)
    }
  }, [reframeTask, reframeTaskId, toast])

  useEffect(() => {
    if (!vcTask || !vcTaskId) return
    if (vcTask.status === 'succeeded' || vcTask.status === 'failed' || vcTask.status === 'cancelled') {
      if (vcTask.status === 'succeeded') toast.success('换声完成，见下方结果')
      else if (vcTask.status === 'failed') toast.error('换声失败：' + (vcTask.error || ''))
      setVcTaskId(null)
    }
  }, [vcTask, vcTaskId, toast])

  useEffect(() => {
    if (!drawTask || !drawTaskId) return
    if (drawTask.status === 'succeeded' || drawTask.status === 'failed' || drawTask.status === 'cancelled') {
      if (drawTask.status === 'succeeded') toast.success('局部重绘完成，见下方结果')
      else if (drawTask.status === 'failed') toast.error('局部重绘失败：' + (drawTask.error || ''))
      setDrawTaskId(null)
    }
  }, [drawTask, drawTaskId, toast])

  const reframeBusy = !!reframeTask && (reframeTask.status === 'pending' || reframeTask.status === 'running')
  const vcBusy = !!vcTask && (vcTask.status === 'pending' || vcTask.status === 'running')

  function doReframe() {
    if (!videoUrl.trim()) return toast.error('请先选择/填写源视频 URL')
    reworkApi.reframe({ video_url: videoUrl.trim(), target_ratio: ratio, resolution: res })
      .then((r) => setReframeTaskId(r.task_id)).catch((e: Error) => toast.error(e.message))
  }
  function doVoiceChange() {
    if (!videoUrl.trim()) return toast.error('请先选择/填写源视频 URL')
    if (!voiceText.trim()) return toast.error('请填写要朗读的台词')
    reworkApi.voiceChange({
      video_url: videoUrl.trim(), text: voiceText.trim(),
      voice_id: voiceId.trim() || null, emotion: emotion.trim() || null,
    }).then((r) => setVcTaskId(r.task_id)).catch((e: Error) => toast.error(e.message))
  }
  const drawBusy = !!drawTask && (drawTask.status === 'pending' || drawTask.status === 'running')
  async function onPickSketch(file: File | undefined) {
    if (!file) return
    try {
      const url = await uploadImage(file)
      setSketchUrl(url)
      toast.success('草图已上传')
    } catch (e) { toast.error((e as Error).message) }
  }
  function doDraw() {
    if (!videoUrl.trim()) return toast.error('请先选择/填写源视频 URL')
    if (!sketchUrl) return toast.error('请先上传编辑后的草图帧')
    reworkApi.drawToVideo({
      video_url: videoUrl.trim(), prompt: drawPrompt.trim() || '按草图的改动重绘画面，保持原片其他内容与人物一致',
      sketch_url: sketchUrl, ratio: drawRatio,
    }).then((r) => setDrawTaskId(r.task_id)).catch((e: Error) => toast.error(e.message))
  }

  function pickVideo(eid: string) {
    setEpisodeId(eid)
    setVideoUrl('')
  }

  return (
    <Modal open={open} onClose={onClose} title="修片（改画幅 / 换声）" size="lg" footer={
      <Button variant="ghost" size="sm" onClick={onClose}>关闭</Button>
    }>
      <div className="space-y-5">
        {/* 选幕 → 取成片 */}
        <div>
          <div className="text-sm font-semibold text-slate-700 mb-1">选择一幕，自动取成片视频</div>
          <div className="flex items-center gap-2">
            <select
              value={episodeId}
              onChange={(e) => pickVideo(e.target.value)}
              className="h-9 flex-1 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40"
            >
              {episodes.map((ep) => (
                <option key={ep.id} value={ep.id}>幕 {ep.index + 1} · {ep.title || '未命名'}</option>
              ))}
            </select>
            <Button variant="outline" size="sm" onClick={() => episodeId && loadVideos(episodeId)} loading={loadingVideos}>
              {loadingVideos ? '读取…' : '取成片'}
            </Button>
          </div>

          {/* 选具体视频片段 */}
          {videos.length > 0 ? (
            <div className="mt-2">
              <div className="text-xs text-slate-400 mb-1">该幕已生成的视频片段（选一个作为源视频）：</div>
              <div className="flex flex-wrap gap-1.5">
                {videos.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setVideoUrl(v.video_url ?? '')}
                    className={'h-7 px-2.5 rounded-lg text-xs border transition-colors ' +
                      (videoUrl === v.video_url
                        ? 'border-brand-500/60 bg-brand-500/10 text-brand-700'
                        : 'border-slate-200 text-slate-600 hover:border-brand-500/40')}
                  >
                    段 {v.index} {v.video_url ? '' : '(无文件)'}
                  </button>
                ))}
              </div>
            </div>
          ) : !loadingVideos ? (
            <div className="mt-2 text-xs text-slate-400">该幕暂无已生成视频（请先生成幕视频），或手动粘贴下方 URL。</div>
          ) : null}
        </div>

        {/* 源视频 URL（自动填充，可改） */}
        <div>
          <div className="text-sm font-semibold text-slate-700 mb-1">源视频 URL</div>
          <input
            value={videoUrl}
            onChange={(e) => setVideoUrl(e.target.value)}
            placeholder="自动取自上方选择的幕；也可手动粘贴"
            className="w-full h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40"
          />
        </div>

        {/* reframe */}
        <div className="rounded-xl border border-slate-200 p-3">
          <div className="flex items-center gap-2 mb-2">
            <Icon name="monitor" size={15} className="text-brand-600" />
            <span className="text-sm font-semibold text-slate-700">改画幅（reframe）</span>
            <Badge variant="blue" size="sm">不重拍</Badge>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <select value={ratio} onChange={(e) => setRatio(e.target.value)}
              className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40">
              {ASPECT_RATIOS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            <select value={res} onChange={(e) => setRes(e.target.value)}
              className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40">
              {RESOLUTIONS.map((r2) => <option key={r2.value} value={r2.value}>{r2.label}</option>)}
            </select>
          </div>
          <Button variant="outline" size="sm" className="mt-2" onClick={doReframe} loading={reframeBusy}>
            {reframeBusy ? '重切中…' : '重切画幅'}
          </Button>
          {reframeTask?.status === 'succeeded' && reframeTask.result_url && (
            <div className="mt-2 text-sm">
              <a href={reframeTask.result_url} target="_blank" rel="noreferrer"
                className="text-brand-600 hover:text-brand-700 break-all">{reframeTask.result_url}</a>
            </div>
          )}
        </div>

        {/* voice-change */}
        <div className="rounded-xl border border-slate-200 p-3">
          <div className="flex items-center gap-2 mb-2">
            <Icon name="volume-2" size={15} className="text-brand-600" />
            <span className="text-sm font-semibold text-slate-700">换声（voice-change）</span>
            <Badge variant="blue" size="sm">画面不动</Badge>
          </div>
          <textarea value={voiceText} onChange={(e) => setVoiceText(e.target.value)}
            placeholder="要朗读的台词/旁白" rows={2}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40" />
          <div className="grid grid-cols-2 gap-2 mt-2">
            <input value={voiceId} onChange={(e) => setVoiceId(e.target.value)} placeholder="voice_id（可空=默认）"
              className="h-8 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40" />
            <input value={emotion} onChange={(e) => setEmotion(e.target.value)} placeholder="情绪（可空）"
              className="h-8 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40" />
          </div>
          <Button variant="outline" size="sm" className="mt-2" onClick={doVoiceChange} loading={vcBusy}>
            {vcBusy ? '换声中…' : '换声'}
          </Button>
          {vcTask?.status === 'succeeded' && vcTask.result_url && (
            <div className="mt-2 text-sm">
              <a href={vcTask.result_url} target="_blank" rel="noreferrer"
                className="text-brand-600 hover:text-brand-700 break-all">{vcTask.result_url}</a>
            </div>
          )}
        </div>

        {/* draw-to-video：局部重绘（H3 Ref2VA 近似） */}
        <div className="rounded-xl border border-amber-200 bg-amber-50/30 p-3">
          <div className="flex items-center gap-2 mb-1">
            <Icon name="pencil" size={15} className="text-amber-600" />
            <span className="text-sm font-semibold text-slate-700">局部重绘（draw-to-video）</span>
            <Badge variant="amber" size="sm">H3 近似</Badge>
          </div>
          <p className="text-[11px] text-slate-500 mb-2">
            提示：编辑源视频的某一帧（如在图上涂改要改的部位），上传为草图帧；系统以
            源视频 + 草图帧重绘整片（跟随原片并按草图改动）。需模型配置 draw-to-video 能力。
          </p>
          <input
            type="file" accept="image/png,image/jpeg,image/webp"
            onChange={(e) => onPickSketch(e.target.files?.[0])}
            className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-amber-100 file:px-3 file:py-1.5 file:text-xs file:text-amber-700 file:cursor-pointer"
          />
          {sketchUrl ? (
            <div className="mt-2 flex items-center gap-2 text-xs text-emerald-600">
              <Icon name="check-circle" size={13} /> 草图已就绪
              <button className="text-slate-400 hover:text-rose-500" onClick={() => setSketchUrl('')}>移除</button>
            </div>
          ) : null}
          <textarea value={drawPrompt} onChange={(e) => setDrawPrompt(e.target.value)}
            placeholder="提示词：如「把夹克改成红色，其余保持原样」"
            rows={2}
            className="w-full mt-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-500/40" />
          <div className="flex items-center gap-2 mt-2">
            <select value={drawRatio} onChange={(e) => setDrawRatio(e.target.value)}
              className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-500/40">
              {ASPECT_RATIOS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            <Button variant="outline" size="sm" onClick={doDraw} loading={drawBusy}>
              {drawBusy ? '重绘中…' : '局部重绘'}
            </Button>
          </div>
          {drawTask?.status === 'succeeded' && drawTask.result_url && (
            <div className="mt-2 text-sm">
              <a href={drawTask.result_url} target="_blank" rel="noreferrer"
                className="text-brand-600 hover:text-brand-700 break-all">{drawTask.result_url}</a>
            </div>
          )}
        </div>

        {(vcBusy || reframeBusy || drawBusy) && (
          <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> 处理中…</div>
        )}
      </div>
    </Modal>
  )
}
