/**
 * 连续长片控制：顶栏「连续长片」按钮 → 弹窗选「幕 + 分辨率 + 画面比例」→ 一键把该幕全部连续分镜生成为无缝整片。
 * 配置全部复用系统已有能力：分辨率用 GEN_RESOLUTIONS（0.1MP~1.0MP 与 480p/720p/768p），比例用项目 aspect_ratio，默认取项目值。
 * 段间衔接帧数由后端默认 22（内部参数，不在前端暴露自造选项）。
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useTaskPoller, formatWait } from '../api/useTaskPoller'
import { Button } from './ui/Button'
import { Badge } from './ui/Badge'
import { ProgressBar } from './ui/ProgressBar'
import { Modal } from './ui/Modal'
import { useToast } from './ui/Toast'
import { Icon } from '../lib/icons'
import { genResolutionOptions, aspectRatioOptions, isGenResolution, DEFAULT_GEN_RESOLUTION, type GenResolution } from '../lib/videoConfig'
import type { Episode, Project } from '../api/types'

const FILM_BADGE: Record<string, { text: string; variant: 'gray' | 'blue' | 'green' | 'red' }> = {
  none: { text: '未生成', variant: 'gray' },
  pending: { text: '排队中', variant: 'gray' },
  running: { text: '生成中', variant: 'blue' },
  done: { text: '已生成', variant: 'green' },
  failed: { text: '失败', variant: 'red' },
  cancelled: { text: '已取消', variant: 'gray' },
}

export function ContinuousFilmControl({ project, disabled }: { project: Project; disabled?: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [res, setRes] = useState<GenResolution>(() => {
    const r = (project.video_params?.res ?? project.resolution) as string | undefined
    return isGenResolution(r) ? r : DEFAULT_GEN_RESOLUTION
  })
  const [ratio, setRatio] = useState(project.aspect_ratio || '16:9')
  const [steps, setSteps] = useState<string>(() => {
    const s = project.video_params?.steps
    return s !== null && s !== undefined ? String(s) : ''
  })
  const [turbo, setTurbo] = useState<string>(() => {
    const t = project.video_params?.turbo
    return t ? String(t) : ''
  })
  const [highQuality, setHighQuality] = useState(false)
  const [epId, setEpId] = useState('')
  const [taskId, setTaskId] = useState<string | null>(null)

  const { data: episodes = [] } = useQuery({
    queryKey: ['project_episodes', project.id],
    queryFn: () => api.get<Episode[]>(`/projects/${project.id}/episodes`),
  })
  const activeEp = episodes.find((e) => e.id === epId) ?? episodes[0] ?? null
  const poll = useTaskPoller(taskId, { detailed: true })
  const active = !!taskId && (poll.task?.status === 'pending' || poll.task?.status === 'running')

  useEffect(() => {
    const st = poll.task?.status
    if (st === 'succeeded' || st === 'failed' || st === 'cancelled') {
      qc.invalidateQueries({ queryKey: ['project_episodes', project.id] })
      if (st === 'succeeded') toast.success('连续长片生成完成')
      else if (st === 'failed') toast.error('连续长片生成失败：' + (poll.task?.error || ''))
      setTaskId(null)
    }
  }, [poll.task?.status, project.id, qc, toast])

  const mut = useMutation({
    mutationFn: (eid: string) =>
      api.post<{ task_id: string }>(`/episodes/${eid}/continuous-film`, {
        config: {
          res, ratio, context_enabled: true,
          ...(steps !== '' ? { steps: Number(steps) } : {}),
          ...(turbo ? { turbo } : {}),
          ...(highQuality ? { high_quality: true } : {}),
        },
      }),
    onSuccess: (r) => setTaskId(r.task_id),
    onError: (e: Error) => toast.error(e.message),
  })

  const confirm = () => { if (activeEp) mut.mutate(activeEp.id) }
  const b = FILM_BADGE[activeEp?.continuous_film_status ?? 'none'] ?? FILM_BADGE.none
  const filmDone = activeEp?.continuous_film_status === 'done' && !!activeEp.continuous_film_url

  return (
    <>
      <Button variant="outline" size="sm" leftIcon={<Icon name="film" size={13} />}
        loading={active || mut.isPending} disabled={disabled} onClick={() => setOpen(true)}>
        连续长片
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title="生成连续长片（一集无缝衔接）" size="md"
        footer={
          <div className="flex items-center gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={() => setOpen(false)}>取消</Button>
            <Button variant="primary" size="sm" loading={active || mut.isPending} disabled={!activeEp} onClick={confirm}>
              {active ? '生成中…' : '生成连续长片'}
            </Button>
          </div>
        }>
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1">选择幕（本幕全部连续分镜一次无缝出片）</label>
            <select value={activeEp?.id ?? ''} onChange={(e) => setEpId(e.target.value)} disabled={active}
              className="w-full h-9 rounded-md border border-slate-200 bg-white text-sm px-2 text-slate-700 outline-none focus:border-blue-400">
              {episodes.map((e) => <option key={e.id} value={e.id}>{e.index}. {e.title}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">分辨率</label>
              <select value={res} onChange={(e) => setRes(e.target.value as GenResolution)} disabled={active}
                className="w-full h-9 rounded-md border border-slate-200 bg-white text-sm px-2 text-slate-700 outline-none focus:border-blue-400">
                {genResolutionOptions(true).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">画面比例</label>
              <select value={ratio} onChange={(e) => setRatio(e.target.value)} disabled={active}
                className="w-full h-9 rounded-md border border-slate-200 bg-white text-sm px-2 text-slate-700 outline-none focus:border-blue-400">
                {aspectRatioOptions(res).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">推理步数</label>
              <select value={steps} onChange={(e) => setSteps(e.target.value)} disabled={active}
                className="w-full h-9 rounded-md border border-slate-200 bg-white text-sm px-2 text-slate-700 outline-none focus:border-blue-400">
                <option value="">跟随默认</option>
                {[4, 6, 8, 16, 20].map((n) => <option key={n} value={n}>{n} 步</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Turbo 模式</label>
              <select value={turbo} onChange={(e) => setTurbo(e.target.value)} disabled={active}
                className="w-full h-9 rounded-md border border-slate-200 bg-white text-sm px-2 text-slate-700 outline-none focus:border-blue-400">
                <option value="">跟随默认</option>
                <option value="high">Turbo 高</option>
                <option value="mid">Turbo 中</option>
                <option value="low">Turbo 低</option>
              </select>
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={highQuality} onChange={(e) => setHighQuality(e.target.checked)} disabled={active}
              className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-400" />
            高质量模式（更高保真·步数升到16；更真实脸，生成更慢）
          </label>
          {active && (
            <ProgressBar value={poll.task?.progress ?? 0}
              label={`连续长片生成中 ${poll.task?.progress ?? 0}%${poll.waitSeconds > 0 ? ` · 已等待 ${formatWait(poll.waitSeconds)}` : ''}`}
              variant="blue" />
          )}
          <div className="flex items-center gap-2">
            <Badge variant={b.variant} dot>{b.text}</Badge>
            {activeEp?.continuous_film_duration ? <span className="text-xs text-slate-400">{activeEp.continuous_film_duration.toFixed(1)}s</span> : null}
          </div>
          {filmDone && (
            <div>
              <video src={activeEp.continuous_film_url!} controls className="w-full rounded-lg border border-slate-200 bg-black max-h-72" />
              <a href={activeEp.continuous_film_url!} target="_blank" rel="noreferrer" className="text-xs text-blue-600 underline mt-1 inline-block">在浏览器新标签打开 / 下载</a>
            </div>
          )}
          <p className="text-[11px] text-slate-400">该幕全部连续分镜一次生成、段间运动/音频自动衔接（AIMixer 导演台）。逐镜打磨/重试走单镜面板。分辨率沿用项目档位（0.1MP~1.0MP 级联档）。</p>
        </div>
      </Modal>
    </>
  )
}