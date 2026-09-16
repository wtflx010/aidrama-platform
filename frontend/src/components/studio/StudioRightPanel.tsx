/** 分镜工作台 · 右列：分镜详情 + 生成控制面板 */
import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, uploadImage } from '../../api/client'
import { useTaskPoller } from '../../api/useTaskPoller'
import type { Project, Segment, ShotBeat, VideoClip } from '../../api/types'
import { SegmentAssetBinder } from '../SegmentAssetBinder'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { ProgressBar } from '../ui/ProgressBar'
import { useToast } from '../ui/Toast'
import { Icon } from '../../lib/icons'
import { DEFAULT_GEN_RESOLUTION as DEFAULT_RES, genResolutionOptions, isGenResolution } from '../../lib/videoConfig'
import { cn } from '../../lib/cn'

const SHOT_TYPES = ['远景', '全景', '中景', '近景', '特写']
const CAMERAS = ['固定', '推', '拉', '摇', '移', '跟']

const FPS_OPTIONS = ['16', '24', '30', '60']
// 生成档位（走 lib/videoConfig 的 GEN_RESOLUTIONS：0.1MP~1.0MP 统一级联档；1080p 是超分档，不能当作生成档）
const RES_OPTIONS = genResolutionOptions()
// 画面比例（尺寸随项目分辨率档位换算，不做 720P 固定标注）
const SIZE_OPTIONS = [
  { value: '16:9', label: '16:9' },
  { value: '9:16', label: '9:16' },
  { value: '1:1', label: '1:1' },
  { value: '4:3', label: '4:3' },
  { value: '3:4', label: '3:4' },
]
const STEPS_OPTIONS = [
  { value: '4', label: '4步 Turbo' },
  { value: '6', label: '6步' },
  { value: '8', label: '8步' },
  { value: '16', label: '16步' },
  { value: '20', label: '20步' },
]
const CFG_OPTIONS = [
  { value: '1.0', label: '1.0（默认）' },
  { value: '2.0', label: '2.0' },
  { value: '3.0', label: '3.0' },
  { value: '4.0', label: '4.0' },
  { value: '5.0', label: '5.0' },
]
const TURBO_OPTIONS = [
  { value: 'high', label: 'Turbo 高' },
  { value: 'mid', label: 'Turbo 中' },
  { value: 'low', label: 'Turbo 低' },
]
const REF_OPTIONS = [
  { value: 'none', label: '无' },
  { value: 'upload', label: '手动上传' },
  { value: 'prev_tail', label: '上一分镜尾帧' },
]
interface TimelineShot { segment_id: string; episode_index: number; segment_index: number; title: string | null; start_ms: number; end_ms: number; duration: number }
interface TimelineData { total_duration_s: number; project_timeline: TimelineShot[]; script_timeline: TimelineShot[] }

// 进行中的「重新生成分镜」视频任务，跨页面/切页持久化（localStorage）。
// 语义：同一分镜同时只有一个未完成任务；切到其他功能页再回来，仍保持按钮
// disabled + 打转，直到任务进入终态（succeeded/failed/cancelled）才恢复。
const PENDING_GEN_TASK_KEY = 'studio.pendingGenTask.v1'
interface PendingGenTask { segmentId: string; taskId: string; createdAt: number }
function readPendingGenTask(): PendingGenTask | null {
  try {
    const raw = localStorage.getItem(PENDING_GEN_TASK_KEY)
    if (!raw) return null
    const rec = JSON.parse(raw) as PendingGenTask
    return rec && typeof rec.segmentId === 'string' && typeof rec.taskId === 'string' ? rec : null
  } catch {
    return null
  }
}
function writePendingGenTask(rec: PendingGenTask | null) {
  try {
    if (rec) localStorage.setItem(PENDING_GEN_TASK_KEY, JSON.stringify(rec))
    else localStorage.removeItem(PENDING_GEN_TASK_KEY)
  } catch {
    /* localStorage 不可用（隐私模式等）时退化为仅本页生效 */
  }
}
const TASK_TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

interface LibBgm { id: string; project_id: string | null; episode_id: string | null; emotion: string; audio_url: string | null; duration: number; volume: number; status: string; source: string; prompt: string }
interface LibSfx { id: string; project_id: string | null; segment_id: string | null; sfx_type: string; sfx_name: string; audio_url: string | null; duration: number; volume: number; status: string; source: string }

export function RightPanel({ segment, timeline, projectId, project, allClipMap }: {
  segment: Segment | null
  timeline: TimelineData | undefined
  projectId: string | null
  project?: Project | null
  /** 分镜工作台持有的全部分镜视频候选（用于判断本镜是否有成功视频以启用超分） */
  allClipMap?: Record<string, VideoClip[]>
}) {
  const qc = useQueryClient()
  const toast = useToast()

  // 当前分镜的视频生成参数（从 segment.gen_params 读取，缺省用默认）
  const gp = (segment?.gen_params || {}) as Record<string, unknown>
  // 项目级参数（跟随项目档案）：分镜面板未单独设置时继承项目分辨率/画面比例，
  // 未显式改动不写入 gen_params，后端生成时自动回退项目级（避免把项目 480P 覆盖成 720P）
  const projRatio = project?.aspect_ratio || '16:9'
  const projRes = project?.resolution || DEFAULT_RES
  // 2026-08-24：fps/steps/cfg/turbo/seed 同款继承——分镜未单独设置时默认取
  // 项目级 video_params（项目未设=「跟随默认」时用内置默认值），与项目一致不写入
  // gen_params，后端自动回退；仅显式改过才作分镜级覆盖。
  const pgp = (project?.video_params || {}) as Record<string, unknown>
  const _projDefault = (k: string, fb: string) => {
    const v = pgp[k]
    return v === '' || v == null ? fb : String(v)
  }
  const projFps = _projDefault('fps', '24')
  const projSteps = _projDefault('steps', '8')
  const projCfg = _projDefault('cfg', '1.0')
  const projTurbo = _projDefault('turbo', 'mid')
  const projSeed = pgp.seed === '' || pgp.seed == null ? '' : String(pgp.seed)
  // 本地编辑态：所有改动先存这里，统一点「保存」后一次落库
  const [title, setTitle] = useState(segment?.title ?? '')
  const [shotType, setShotType] = useState(segment?.shot_type ?? '中景')
  const [camera, setCamera] = useState(segment?.camera ?? '固定')
  const [desc, setDesc] = useState(segment?.description ?? '')
  const [narration, setNarration] = useState(segment?.narration ?? '')
  const [dialogue, setDialogue] = useState(segment?.dialogue ?? '')
  const [duration, setDuration] = useState(segment?.duration ?? 5)
  // 2026-08-28 分镜内多镜头运镜节拍：本地编辑态（seg.shot_beats 初始化，保存时整体落库）
  // beats 始终保留用户编辑内容（关掉时间分段只是不提交，重新开启不丢）；
  // beatsEnabled=false 时保存提交 shot_beats:[]（整镜单镜头，沿用下方景别/运镜单值）
  const [beats, setBeats] = useState<ShotBeat[]>(() => (segment?.shot_beats?.length ? segment.shot_beats : []))
  const [beatsEnabled, setBeatsEnabled] = useState<boolean>((segment?.shot_beats?.length ?? 0) > 0)
  const [fps, setFps] = useState<string>(String(gp.fps ?? projFps))
  // 清晰度初始值：历史遗留的非法生成档（如旧下拉存过的 1080p）自动钳回项目档位，
  // 避免下拉不可见值被原样写进 gen_params 造成覆盖（lib/videoConfig 铁律：生成档为 0.1MP~1.0MP 统一级联档）
  const [res, setRes] = useState<string>(() => {
    const raw = String(gp.res ?? projRes)
    return isGenResolution(raw) ? raw : projRes
  })
  const [videoSize, setVideoSize] = useState<string>(String(gp.video_size ?? projRatio))
  const [steps, setSteps] = useState<string>(String(gp.steps ?? projSteps))
  const [cfg, setCfg] = useState<string>(String(gp.cfg ?? projCfg))
  const [seed, setSeed] = useState<string>(String(gp.seed ?? projSeed))
  const [turbo, setTurbo] = useState<string>(String(gp.turbo ?? projTurbo))
  const [refSrc, setRefSrc] = useState<string>(String(gp.reference_src ?? 'none'))
  const [customFrame, setCustomFrame] = useState<string>(String(gp.custom_first_frame_url ?? ''))
  const [uploading, setUploading] = useState(false)
  // 正在生成的视频任务 id：初始值从 localStorage 恢复（切页回来仍处于「生成中」，
  // 按钮保持 disabled + 打转，不允许再次点击重复派发任务）。
  const [genTaskId, setGenTaskIdState] = useState<string | null>(() => {
    const rec = readPendingGenTask()
    return rec && segment && rec.segmentId === segment.id ? rec.taskId : null
  })
  const [genError, setGenError] = useState<string | null>(null)
  const [structuring, setStructuring] = useState(false)
  const [structureError, setStructureError] = useState<string | null>(null)
  const [structuredPrompt, setStructuredPrompt] = useState<string | null>(null)
  // 结构化正文语言：zh=简体中文六段式（保存/展示中文，生成仍走英文）；en=英文（中英对照）
  const [structLang, setStructLang] = useState<'zh' | 'en'>('zh')
  const genTask = useTaskPoller(genTaskId)
  const genBusy = genTask && (genTask.status === 'pending' || genTask.status === 'running')

  // 切换分镜时：恢复该分镜的进行中任务（localStorage 只存最近一条，按 segmentId 对号）
  // 本地戏份：切到无进行中任务的分镜 → 清空；切回原分镜 → 自动恢复 busy + 打转
  useEffect(() => {
    const rec = readPendingGenTask()
    setGenTaskIdState(rec && segment && rec.segmentId === segment.id ? rec.taskId : null)
    // 切换分镜时清空上一镜的 LLM 结构化结果（避免串镜展示）
    setStructuredPrompt(null)
    setStructureError(null)
    setStructuring(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segment?.id])

  // 任务进入终态（成功/失败/取消）→ 清除持久化记录，按钮恢复可点，
  // 并实时刷新视频/分镜数据。此前 regenerate 只在「派发任务时」invalidate 一次，
  // 那时视频尚未生成，任务完成后列表仍是旧的 → 前端看不到新视频，必须手动刷新整页；
  // 这里在终态再 invalidate，任务一完成列表即重新拉取并展示最新视频。
  useEffect(() => {
    if (genTask && TASK_TERMINAL.has(genTask.status)) {
      setGenTaskIdState(null)
      writePendingGenTask(null)
      qc.invalidateQueries({ queryKey: ['studio-clips'] })
      qc.invalidateQueries({ queryKey: ['videos'] })
      qc.invalidateQueries({ queryKey: ['segments'] })
      qc.invalidateQueries({ queryKey: ['project'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genTask?.status])

  // ── 超分本镜（单镜 480p → 1080p，4x/2x 档位可选）──
  // 与「重新生成分镜」同款持久化：localStorage 存进行中任务，切页/切镜回来仍保持打转禁点
  const UPSCALE_TASK_KEY = 'studio.pendingUpscaleTask.v1'
  const [upsTaskId, setUpsTaskIdState] = useState<string | null>(() => {
    try {
      const raw = localStorage.getItem(UPSCALE_TASK_KEY)
      if (!raw) return null
      const rec = JSON.parse(raw) as { segmentId: string; taskId: string }
      return rec && segment && rec.segmentId === segment.id ? rec.taskId : null
    } catch {
      return null
    }
  })
  const [upsTier, setUpsTier] = useState<string>(
    String(((segment?.gen_params as Record<string, unknown> | undefined) || {}).upscale_tier ?? '4x'),
  )
  const upsTask = useTaskPoller(upsTaskId)
  const upsBusy = !!upsTask && (upsTask.status === 'pending' || upsTask.status === 'running')

  // 切镜时按 segmentId 对号恢复/清空超分任务
  useEffect(() => {
    try {
      const raw = localStorage.getItem(UPSCALE_TASK_KEY)
      if (raw) {
        const rec = JSON.parse(raw) as { segmentId: string; taskId: string }
        setUpsTaskIdState(rec && segment && rec.segmentId === segment.id ? rec.taskId : null)
      } else {
        setUpsTaskIdState(null)
      }
    } catch {
      setUpsTaskIdState(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segment?.id])

  // 超分终态：清除持久化 + 刷新视频列表（前端立即看到超清版）
  useEffect(() => {
    if (upsTask && TASK_TERMINAL.has(upsTask.status)) {
      setUpsTaskIdState(null)
      try { localStorage.removeItem(UPSCALE_TASK_KEY) } catch { /* ignore */ }
      qc.invalidateQueries({ queryKey: ['studio-clips'] })
      qc.invalidateQueries({ queryKey: ['videos'] })
      qc.invalidateQueries({ queryKey: ['segments'] })
      if (upsTask.status === 'succeeded') toast.success('本镜已超分为高清')
      else if (upsTask.status === 'failed') toast.error('超分失败：' + (upsTask.error || '详情见任务中心'))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [upsTask?.status])

  // 写入任务的唯一入口：同步 state 与 localStorage
  const applyGenTaskId = useCallback((id: string | null) => {
    setGenTaskIdState(id)
    writePendingGenTask(id && segment ? { segmentId: segment.id, taskId: id, createdAt: Date.now() } : null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segment?.id])

  // 保存 gen_params（hook 必须无条件调用，早退前声明）
  const saveGp = useMutation({
    mutationFn: (patch: Record<string, unknown>) => {
      const sid = segment ? segment.id : ''
      // 后端仅提供 PUT /segments/{id}（按“只更新传入字段”语义处理），
      // 之前误用 PATCH → 405 Method Not Allowed；改用 PUT。
      return api.put('/segments/' + sid, patch)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['segments'] }); qc.invalidateQueries({ queryKey: ['project'] }); qc.invalidateQueries({ queryKey: ['studio-clips'] }) },
  })

  // 全局音频库（BGM / SFX，与中列横链共用同一 queryKey）
  const { data: lib } = useQuery({
    queryKey: ['audio-library'],
    queryFn: () => api.get<{ bgm: LibBgm[]; sfx: LibSfx[] }>('/audio/library'),
  })

  // 绑定音频（BGM→所属幕，SFX→当前分镜）；成功后刷新音频库（中列横链同步可见）
  const bindAudio = useMutation({
    mutationFn: (body: { bgm_id?: string; sfx_id?: string; clear_bgm?: boolean; clear_sfx?: boolean }) =>
      api.post('/segments/' + (segment?.id || '') + '/audio/bind', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['audio-library'] })
      qc.invalidateQueries({ queryKey: ['segments'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })


  if (!segment) {
    return (
      <div className="p-6">
        <EmptyState icon="film" title="未选择分镜" description="在左侧或分镜横链中选择一个分镜" />
      </div>
    )
  }
  const seg = segment as Segment

  // 本镜是否存在已成功视频（超分本镜前置条件；超清版也算已成功）
  const segClips = allClipMap?.[seg.id] ?? []
  const hasSucceededClip = segClips.some((v) => v.status === 'succeeded')

  function buildGenParams() {
    const g: Record<string, unknown> = { ...(seg.gen_params || {}) }
    // 统一规则：与项目级（或内置默认）一致的值不写入 gen_params，后端生成时
    // 自动回退项目级（跟随项目后续改动）；仅用户显式挑选的不同档位作为分镜级覆盖。
    if (fps === projFps) delete g.fps
    else g.fps = Number(fps) || 24
    if (res === projRes) delete g.res
    else g.res = res
    if (videoSize === projRatio) delete g.video_size
    else g.video_size = videoSize
    if (steps === projSteps) delete g.steps
    else g.steps = steps === '' ? null : Number(steps)
    if (cfg === projCfg) delete g.cfg
    else g.cfg = cfg === '' ? null : Number(cfg)
    if (seed === projSeed) delete g.seed
    else g.seed = seed === '' ? null : Number(seed)
    if (turbo === projTurbo) delete g.turbo
    else g.turbo = turbo
    g.reference_src = refSrc
    g.custom_first_frame_url = refSrc === 'upload' ? (customFrame || null) : null
    return g
  }

  // 单个下拉改动仅更新本地状态（统一由「保存」落库，避免逐个弹提示）
  function setGp(key: string, value: string) {
    setGenError(null)
    const setters: Record<string, (v: string) => void> = {
      fps: setFps,
      res: setRes,
      video_size: setVideoSize,
      steps: setSteps,
      cfg: setCfg,
      turbo: setTurbo,
      reference_src: setRefSrc,
    }
    setters[key]?.(value)
  }

  // ── 节拍编辑（本地状态，保存时随 PUT 落库；后端仍会做最终归一化）──
  const clampBeat = (v: number) => Math.max(0, Math.min(Number(duration) || 5, Number.isFinite(v) ? v : 0))
  function addBeat() {
    setBeats((prev) => {
      const d = Number(duration) || 5
      const n = prev.length
      if (n === 0) return [{ start_sec: 0, end_sec: d, shot_type: shotType || '中景', camera: camera || '固定', content: '' }]
      const last = prev[n - 1]
      return [...prev.slice(0, -1), { ...last, end_sec: clampBeat((last.start_sec + last.end_sec) / 2) }, { start_sec: clampBeat((last.start_sec + last.end_sec) / 2), end_sec: d, shot_type: last.shot_type, camera: last.camera, content: '' }]
    })
  }
  function updateBeat(i: number, patch: Partial<ShotBeat>) {
    setBeats((prev) => prev.map((b, idx) => (idx === i ? { ...b, ...patch } : b)))
  }
  function removeBeat(i: number) {
    setBeats((prev) => prev.filter((_, idx) => idx !== i))
  }
  // 只读时间轴分段配色（暗色系可读）
  const BEAT_COLORS = ['#6366f1', '#8b5cf6', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#14b8a6', '#f97316', '#06b6d4']

  async function saveAll(): Promise<boolean> {
    setGenError(null)
    try {
      await saveGp.mutateAsync({
        title: title.trim(),
        shot_type: shotType,
        camera: camera,
        description: desc,
        narration: narration,
        dialogue: dialogue,
        duration: duration,
        gen_params: buildGenParams(),
        // 2026-08-28：时间分段运镜。开启 → 提交节拍列表（后端归一化）；
        // 关闭 → 提交 []（整镜单镜头）。同时用首拍景别/运镜同步单值字段，
        // 保持列表徽标/幕级产物展示一致。
        shot_beats: beatsEnabled ? beats : [],
        ...(beatsEnabled && beats.length ? { shot_type: beats[0].shot_type, camera: beats[0].camera } : {}),
      })
      toast.success('分镜参数已保存')
      return true
    } catch (e) {
      const msg = (e as Error)?.message || '保存失败'
      setGenError(msg)
      toast.error(msg)
      return false
    }
  }

  // ── 重新生成当前分镜 ──
  function regenerate() {
    setGenError(null)
    applyGenTaskId(null)
    const fpsN = Number(fps) || 24
    // 后端要求 num_frames ∈ [9,441] 且满足 8n+1（如 121/241/441）。向上对齐，避免 422。
    // 修复：此前用 Math.round(fps*duration)（如 24fps×5s=120）不满足 8n+1，后端 422
    // 校验失败 → 前端曾显示 "[object Object]"（detail 数组未归一化，可读性为 0）。
    let n = Math.round(fpsN * duration)
    n = Math.max(9, Math.min(441, n))
    n = Math.min(441, Math.ceil((n - 1) / 8) * 8 + 1)
    api.post<{ task: { id: string } }>('/segments/' + seg.id + '/videos/generate', {
      keyframe_id: null,
      num_frames: n,
      frame_rate: fpsN,
    }).then((r) => {
      // 记录进行中任务 id：立即生效并写入 localStorage（切页回来仍保持 busy 状态）
      applyGenTaskId((r.task && r.task.id) || null)
      qc.invalidateQueries({ queryKey: ['studio-clips'] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
    }).catch((e: Error) => {
      setGenError(e.message)
      toast.error(e.message)
    })
  }

  // ── 重新生成：先保存参数，保存成功后才触发生成；保存失败中止并明确提示 ──
  // 修复：此前按钮 onClick 同时 fire 了 saveAll() 与 regenerate()——用户只点了
  // 「重新生成分镜」却被静默保存参数，且保存/生成任一失败都只会报不可读错误。
  async function saveAndRegenerate() {
    const saved = await saveAll()
    if (!saved) return
    regenerate()
  }

  // ── 超分本镜：以当前分镜已生成的视频为基础 480p → 1080p ──
  function upscaleClip() {
    setGenError(null)
    api.post<{ task: { id: string } }>('/segments/' + seg.id + '/videos/upscale', { tier: upsTier })
      .then((r) => {
        const tid = r.task?.id
        if (tid) {
          setUpsTaskIdState(tid)
          try {
            localStorage.setItem(UPSCALE_TASK_KEY, JSON.stringify({ segmentId: seg.id, taskId: tid, createdAt: Date.now() }))
          } catch { /* ignore */ }
        }
        qc.invalidateQueries({ queryKey: ['studio-clips'] })
        qc.invalidateQueries({ queryKey: ['tasks'] })
      })
      .catch((e: Error) => { setGenError(e.message); toast.error(e.message) })
  }

  // ── LLM 提示词结构化（MiniMax H3 Ref2VA 六段式） ──
  // 交互反馈：点击即进入 loading（按钮转圈+禁用防重入），成功展示结构化结果+
  // 高亮提示，失败在按钮下方就地显示原因 + toast，避免"点了没反应"。
  // 结构化结果以「中英对照」写回分镜提示词并落库保存——切镜不再丢失，
  // 重新生成视频时 worker 使用同源英文六段式缓存，确保按结构化结果出片。
  function structurePrompt() {
    if (structuring) return
    setStructuredPrompt(null)
    setStructureError(null)
    setStructuring(true)
    api.post<{ enhanced_prompt: string; negative_prompt: string; bilingual_prompt?: string }>('/segments/' + seg.id + '/enhance-prompt', {
      prompt: seg.description || '',
      target: 'video',
      force: true,
      lang: structLang,
    }).then((r) => {
      if (!r.enhanced_prompt) {
        throw new Error('结构化未返回内容，请稍后重试')
      }
      const bilingual = r.bilingual_prompt || r.enhanced_prompt
      // 同步本地编辑态 & 展示区：结构化结果已保存到分镜提示词（后端已落库）
      setDesc(bilingual)
      setStructuredPrompt(bilingual)
      toast.success(structLang === 'zh'
        ? '已按 MiniMax H3 六段式结构化（简体中文），并写入分镜提示词'
        : '已按 MiniMax H3 六段式结构化（中英对照），并写入分镜提示词')
    }).catch((e: Error) => {
      const msg = (e as Error)?.message || '结构化失败'
      setStructureError(msg)
      toast.error(msg)
    }).finally(() => {
      setStructuring(false)
    })
  }

  // ── 初始帧手动上传 ──
  async function uploadCustomFrame(files: FileList | null) {
    if (!files || files.length === 0) return
    setUploading(true)
    try {
      const url = await uploadImage(files[0])
      setCustomFrame(url)
      setRefSrc('upload')
      setGenError(null)
    } catch (e) {
      setGenError((e as Error).message)
      toast.error((e as Error).message)
    } finally {
      setUploading(false)
    }
  }

  // 可用音频（仅已完成、有音频地址的）
  const doneBgms = (lib?.bgm ?? []).filter((b) => b.status === 'done' || b.status === 'succeeded')
  const doneSfxs = (lib?.sfx ?? []).filter((s) => s.status === 'done' || s.status === 'succeeded')

  // 时间线：当前镜位置
  const tl = timeline?.project_timeline?.find((t) => t.segment_id === segment.id)
  const scriptTl = timeline?.script_timeline?.find((t) => t.segment_id === segment.id)

  return (
    <div className="w-80 shrink-0 rounded-xl border border-slate-200 bg-white flex flex-col overflow-hidden">
      {/* 可滚动内容区 */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
      {/* 标题 + 结构化 */}
      <section>
        <label className="block text-xs text-slate-500 mb-1.5 flex items-center gap-1"><Icon name="type" size={12} /> 分镜标题</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)}
          placeholder="输入 4 字分镜标题" className="input-base text-sm font-medium" />
      </section>

      {/* 运镜与节拍：整镜单镜头（景别/运镜） 或 时间分段多镜头运镜 */}
      <section>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-slate-500 flex items-center gap-1"><Icon name="video" size={12} /> 运镜与节拍</label>
          <button
            type="button"
            onClick={() => setBeatsEnabled((v) => !v)}
            className={cn(
              'text-[11px] font-medium inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-all duration-150 select-none active:scale-95',
              beatsEnabled
                ? 'text-brand-600 bg-brand-500/10 hover:bg-brand-500/20'
                : 'text-slate-500 hover:bg-slate-100',
            )}
            title={beatsEnabled ? '回到整镜单镜头的景别/运镜，节拍内容保留但不生效' : '把本镜拆成 2~3 段时间，每段单独配景别/运镜/画面，出片按 [Shot N] 时间码切镜'}
          >
            <Icon name="clock" size={11} />
            {beatsEnabled ? '时间分段已开启' : '开启时间分段运镜'}
          </button>
        </div>

        {!beatsEnabled ? (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">景别</label>
              <select value={shotType} onChange={(e) => setShotType(e.target.value)} className="input-base text-sm">
                {SHOT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">运镜</label>
              <select value={camera} onChange={(e) => setCamera(e.target.value)} className="input-base text-sm">
                {CAMERAS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {/* 只读时间轴：按各拍时长比例分段着色 */}
            <div className="flex h-6 rounded-md overflow-hidden border border-slate-300 bg-slate-100" title="各节拍占本镜时长的比例（只读示意，起止秒在下方编辑）">
              {beats.length === 0 ? (
                <div className="flex-1 flex items-center justify-center text-[10px] text-slate-400">点击「添加节拍」开始分段</div>
              ) : beats.map((b, i) => {
                const bd = Math.max(0.5, ((b.end_sec ?? 0) - (b.start_sec ?? 0)) / Math.max(Number(duration) || 5, 0.01) * 100)
                return (
                  <div key={i} className="relative flex items-center justify-center overflow-hidden" style={{ flexGrow: bd, backgroundColor: BEAT_COLORS[i % BEAT_COLORS.length] }}>
                    <span className="text-[9px] font-semibold text-white drop-shadow">{i + 1}</span>
                    {i > 0 && <span className="absolute left-0 top-0 h-full w-px bg-white/70" />}
                  </div>
                )
              })}
            </div>
            <div className="flex justify-between text-[10px] text-slate-400"><span>0s</span><span>{duration || 5}s</span></div>
            {beats.length === 0 && (
              <div className="text-[11px] text-slate-400 bg-slate-50 border border-dashed border-slate-200 rounded-lg px-2 py-1.5">
                已开启时间分段。添加节拍后按 0~{duration || 5}s 分段编辑每段时间的画面/景别/运镜，出片按 H3 [Shot N] 时间码切镜。
              </div>
            )}
            {beats.map((b, i) => (
              <div key={i} className="rounded-lg border border-slate-200 bg-white p-2 space-y-1.5">
                <div className="flex items-center gap-1 text-[11px] text-slate-500">
                  <span className="w-4 h-4 rounded text-center leading-4 text-[10px] font-semibold text-white shrink-0" style={{ backgroundColor: BEAT_COLORS[i % BEAT_COLORS.length] }}>{i + 1}</span>
                  <span className="mr-0.5">节拍 {i + 1}</span>
                  <input type="number" min={0} step={0.5} value={b.start_sec}
                    onChange={(e) => updateBeat(i, { start_sec: Number(e.target.value) })}
                    className="w-14 input-base text-xs px-1 py-0.5" title="开始秒（该拍进入的时间点）" />
                  <span>~</span>
                  <input type="number" min={0} step={0.5} value={b.end_sec}
                    onChange={(e) => updateBeat(i, { end_sec: Number(e.target.value) })}
                    className="w-14 input-base text-xs px-1 py-0.5" title="结束秒（该拍退出的时间点）" />
                  <span>秒</span>
                  <button type="button" onClick={() => removeBeat(i)} disabled={beats.length <= 1}
                    className="ml-auto text-rose-500 hover:text-rose-600 disabled:opacity-30 transition-colors"
                    title={beats.length <= 1 ? '至少保留 1 个节拍' : '删除该节拍'}>
                    <Icon name="trash" size={11} />
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                  <select value={b.shot_type} onChange={(e) => updateBeat(i, { shot_type: e.target.value })} className="input-base text-xs px-1.5 py-1">
                    {SHOT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <select value={b.camera} onChange={(e) => updateBeat(i, { camera: e.target.value })} className="input-base text-xs px-1.5 py-1">
                    {CAMERAS.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <input value={b.content} onChange={(e) => updateBeat(i, { content: e.target.value })}
                  placeholder="该时段画面内容（如：林浅推门而入）" className="input-base text-xs px-1.5 py-1 w-full" />
              </div>
            ))}
            <button type="button" onClick={addBeat}
              className="w-full text-[11px] font-medium inline-flex items-center justify-center gap-1 rounded-md border border-dashed border-slate-300 text-slate-500 py-1.5 hover:border-brand-400 hover:text-brand-600 active:scale-[0.99] transition-all duration-150 select-none">
              <Icon name="plus" size={11} /> 添加节拍
            </button>
            <div className="text-[10px] text-slate-400 leading-4">
              节拍时间需连续覆盖 0~{duration || 5}s（保存时后端自动收敛补齐空档/重叠）。每拍 = 一个内部镜头，出片按 [Shot N] At MM:SS.mmm 时间码切镜。
            </div>
          </div>
        )}
      </section>

      {/* 画面描述 = 提示词 */}
      <section>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-slate-500 flex items-center gap-1"><Icon name="eye" size={12} /> 分镜提示词</label>
          {/* LLM 结构化：按压触感（active:scale）+ hover 底色 + 执行中转圈反馈；语言可切中文/英文 */}
          <div className="flex items-center gap-1">
            <select
              value={structLang}
              onChange={(e) => setStructLang(e.target.value as 'zh' | 'en')}
              disabled={structuring}
              title={structLang === 'zh' ? '简体中文六段式（正文保存为中文，生成仍按英文出片）' : '英文六段式 + 中文段标题（中英对照）'}
              className="text-[10px] text-slate-500 bg-white border border-slate-200 rounded-md px-1 py-0.5 focus:outline-none disabled:opacity-50"
            >
              <option value="zh">中文</option>
              <option value="en">English</option>
            </select>
            <button
              onClick={structurePrompt}
              disabled={structuring}
              className={cn(
                'text-[11px] font-medium inline-flex items-center gap-1 rounded-md px-1.5 py-0.5',
                'transition-all duration-150 select-none',
                'active:scale-95',
                structuring
                  ? 'text-slate-400 cursor-wait'
                  : 'text-brand-600 hover:bg-brand-500/10 hover:text-brand-700 active:bg-brand-500/20',
              )}
            >
              <Icon
                name={structuring ? 'loader-2' : 'sparkles'}
                size={11}
                className={structuring ? 'animate-spin' : 'inline'}
              />
              {structuring ? 'H3 结构化中…' : 'LLM 结构化'}
            </button>
          </div>
        </div>
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)}
          rows={4} className="input-base resize-none text-sm" placeholder="描述这一镜拍什么…" />

        {/* 结构化执行反馈：失败原因就地展示 */}
        {structureError && (
          <div className="mt-2 flex items-start gap-1.5 text-[11px] text-rose-600 bg-rose-50 border border-rose-200 rounded-lg px-2 py-1.5">
            <Icon name="alert-circle" size={11} className="shrink-0 mt-0.5" />
            <span className="break-all">结构化失败：{structureError}</span>
          </div>
        )}
        {structuring && !structuredPrompt && (
          <div className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-2 py-1.5">
            <Icon name="loader-2" size={11} className="animate-spin text-brand-500" />
            正在按 MiniMax H3 规范分析镜头内容并生成六段式提示词…
          </div>
        )}

        {/* 旁白 / 对白 */}
        <div className="space-y-2 mt-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1.5 flex items-center gap-1"><Icon name="mic" size={12} /> 旁白</label>
            <textarea value={narration} onChange={(e) => setNarration(e.target.value)}
              rows={2} className="input-base resize-none text-sm" placeholder="旁白内容（无则留空）" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5 flex items-center gap-1"><Icon name="captions" size={12} /> 对白</label>
            <textarea value={dialogue} onChange={(e) => setDialogue(e.target.value)}
              rows={2} className="input-base resize-none text-sm" placeholder="本镜对白（无则留空）" />
          </div>
        </div>
        {structuredPrompt && (
          <div className="mt-2 rounded-lg bg-emerald-50/60 border border-emerald-300/70 p-2 max-h-40 overflow-y-auto">
            <div className="text-[10px] text-emerald-600 mb-1 flex items-center gap-1">
              <Icon name="check-circle" size={11} className="inline" />
              MiniMax H3 六段式结构化 · 已写入分镜提示词（切镜后保留，重新生成将按此出片）
            </div>
            <pre className="text-[11px] text-slate-700 whitespace-pre-wrap leading-5">{structuredPrompt}</pre>
          </div>
        )}
      </section>


      {/* 分镜时点：剧本 + 项目（只读查看，一行展示） */}
      <section>
        <div className="text-xs font-medium text-slate-600 flex items-center gap-1 mb-2"><Icon name="clock" size={12} /> 分镜时点</div>
        <div className="grid grid-cols-2 gap-3">
          <MiniBar label="剧本时间" tl={scriptTl} total={timeline?.total_duration_s} tone="bg-slate-300" />
          <MiniBar label="项目时间" tl={tl} total={timeline?.total_duration_s} tone="bg-brand-500" />
        </div>
      </section>

      {/* 时长控制（3~30s）+ 生成设置 */}
      <section>
        <div className="text-xs font-medium text-slate-600 flex items-center gap-1 mb-2">
          <Icon name="clock" size={12} /> 时长控制
          <span className="ml-auto text-sm font-semibold text-brand-600">{duration}s</span>
        </div>
        <input type="range" min={3} max={30} step={1} value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
          aria-label="分镜时长（秒）"
          aria-valuetext={`${duration} 秒`}
          className="w-full accent-brand-500" />

        {/* 生成参数下拉①（两列） */}
        <div className="grid grid-cols-2 gap-2 mt-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1">帧率</label>
            <select className="input-base text-sm" value={fps} onChange={(e) => { setFps(e.target.value); setGp('fps', e.target.value) }}>
              {FPS_OPTIONS.map((f) => <option key={f} value={f}>{f}fps</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">清晰度</label>
            <select className="input-base text-sm" value={res} onChange={(e) => { setRes(e.target.value); setGp('res', e.target.value) }}>
              {RES_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">视频大小</label>
            <select className="input-base text-sm" value={videoSize} onChange={(e) => { setVideoSize(e.target.value); setGp('video_size', e.target.value) }}>
              {SIZE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">推理步数</label>
            <select className="input-base text-sm" value={steps} onChange={(e) => { setSteps(e.target.value); setGp('steps', e.target.value) }}>
              {STEPS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">引导强度 CFG</label>
            <select className="input-base text-sm" value={cfg} onChange={(e) => { setCfg(e.target.value); setGp('cfg', e.target.value) }}>
              {CFG_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Turbo 模式</label>
            <select className="input-base text-sm" value={turbo} onChange={(e) => { setTurbo(e.target.value); setGp('turbo', e.target.value) }}>
              {TURBO_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
        </div>

        {/* 种子（留空 = 随机） */}
        <div className="mt-2">
          <label className="block text-xs text-slate-500 mb-1">种子 Seed（留空生成时为随机）</label>
          <input
            type="number"
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="留空 = 随机"
            className="input-base text-sm"
          />
        </div>

        {/* 初始帧来源（无 / 手动上传 / 上一分镜尾帧：横向三选一） */}
        <div className="mt-2">
          <label className="block text-xs text-slate-500 mb-1">初始帧来源</label>
          <div className="grid grid-cols-3 gap-1.5">
            {REF_OPTIONS.map((o) => (
              <button
                key={o.value}
                onClick={() => { setRefSrc(o.value); setGp('reference_src', o.value) }}
                className={cn(
                  'py-1.5 rounded-lg border text-xs font-medium transition-colors',
                  refSrc === o.value
                    ? 'bg-brand-500 text-white border-brand-500'
                    : 'bg-white text-slate-600 border-slate-200 hover:border-brand-400',
                )}
              >
                {o.label}
              </button>
            ))}
          </div>
          {refSrc === 'upload' && (
            <div className="mt-2 flex items-center gap-2">
              <label className="cursor-pointer inline-flex">
                <span className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-slate-200 text-xs text-slate-600 hover:border-brand-400">
                  {uploading ? <Icon name="loader-2" size={11} className="animate-spin" /> : <Icon name="upload" size={11} />}
                  {uploading ? '上传中' : '选择首帧图'}
                </span>
                <input type="file" accept="image/*" className="hidden" onChange={(e) => { void uploadCustomFrame(e.target.files); e.target.value = '' }} />
              </label>
              {customFrame && <span className="text-[10px] text-emerald-600 truncate">已上传 ✓</span>}
            </div>
          )}
        </div>
      </section>

      {/* 修改/添加资产：快速调整分镜资产，改动即同步剧本镜头资产绑定 */}
      <section>
        <SegmentAssetBinder segment={segment} projectId={projectId ?? ''} />
      </section>

      {/* 音频来源：选择 BGM（本幕）与 SFX（本镜） */}
      <section>
        <div className="text-xs font-medium text-slate-600 flex items-center gap-1 mb-2"><Icon name="music" size={12} /> 音频来源</div>
        <div className="space-y-2.5">
          <div>
            <label className="block text-xs text-slate-500 mb-1">BGM（本幕背景音乐）</label>
            <select
              className="input-base text-sm"
              value={doneBgms.find((b) => b.episode_id === seg.episode_id)?.id ?? ''}
              onChange={(e) => bindAudio.mutate(e.target.value ? { bgm_id: e.target.value } : { clear_bgm: true })}
            >
              <option value="">不指定</option>
              {doneBgms.map((b) => (
                <option key={b.id} value={b.id}>{b.emotion} · {Math.round(b.duration || 0)}s</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">音效（本镜）</label>
            <select
              className="input-base text-sm"
              value={doneSfxs.find((s) => s.segment_id === seg.id)?.id ?? ''}
              onChange={(e) => bindAudio.mutate(e.target.value ? { sfx_id: e.target.value } : { clear_sfx: true })}
            >
              <option value="">不指定</option>
              {doneSfxs.map((s) => (
                <option key={s.id} value={s.id}>{s.sfx_name}</option>
              ))}
            </select>
          </div>
          {doneBgms.length === 0 && doneSfxs.length === 0 && (
            <p className="text-[10px] text-slate-300">音频库暂无内容，可去全局「音频」页生成/上传</p>
          )}
        </div>
      </section>

      </div>

      {/* 底部固定操作条：重新生成当前分镜（滚动区之外，下方不会有其他内容） */}
      <div className="shrink-0 border-t border-slate-100 p-3 bg-white">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="md" className="flex-1" onClick={() => { void saveAll() }}
            leftIcon={<Icon name="check" size={15} />}>
            保存
          </Button>
          <Button size="md" className="flex-1" onClick={() => { void saveAndRegenerate() }}
            loading={!!genBusy} disabled={!!genBusy}
            leftIcon={<Icon name="refresh" size={15} />}>
            {genBusy ? '重新生成中…' : '重新生成分镜'}
          </Button>
        </div>
        {/* 超分本镜：借已生成视频 480p → 1080p；档位可选（4x 正式 ~9min/镜 · 2x 快速 ~2min/镜） */}
        <div className="mt-2 flex items-center gap-2">
          <select
            value={upsTier}
            onChange={(e) => setUpsTier(e.target.value)}
            className="input-base text-xs w-24 shrink-0"
            title="超分档位：4x 正式（画质最佳，约 9 分钟/镜）；2x 快速（约 2 分钟/镜）"
          >
            <option value="4x">4x 正式</option>
            <option value="2x">2x 快速</option>
          </select>
          <Button
            variant="outline"
            size="md"
            className="flex-1"
            onClick={() => { void upscaleClip() }}
            loading={!!upsBusy}
            disabled={!!upsBusy || !hasSucceededClip}
            leftIcon={<Icon name="hd" size={15} />}
            title={!hasSucceededClip ? '该分镜还没有已生成的视频' : undefined}
          >
            {upsBusy ? '超分中…' : '超分本镜 1080p'}
          </Button>
        </div>
        {upsBusy && upsTask && (
          <div className="mt-2">
            <ProgressBar value={upsTask.progress ?? 0} variant="brand" label={'超分中 ' + (upsTask.progress ?? 0) + '%'} />
            <p className="text-[10px] text-slate-400 mt-1">约 2~9 分钟/镜，完成后自动切到超清版</p>
          </div>
        )}
        {genBusy && genTask && (
          <div className="mt-2">
            <ProgressBar value={genTask.progress ?? 0} variant="brand" label={'生成中 ' + (genTask.progress ?? 0) + '%'} />
            <p className="text-[10px] text-slate-400 mt-1">任务中心可见详细进度</p>
          </div>
        )}
        {genTask?.status === 'succeeded' && (
          <p className="text-xs text-emerald-600 mt-2 flex items-center gap-1"><Icon name="check-circle" size={12} /> 视频已生成，自动切到分镜概览</p>
        )}
        {genError && <p className="text-xs text-rose-600 mt-2 flex items-center gap-1"><Icon name="alert-circle" size={12} /> {genError}</p>}

      </div>
    </div>
  )

  function MiniBar({ label, tl, total, tone }: { label: string; tl?: TimelineShot; total?: number; tone: string }) {
    const totalS = total || 0
    const startPct = totalS > 0 ? ((tl?.start_ms ?? 0) / 1000 / totalS) * 100 : 0
    const widthPct = totalS > 0 ? ((tl ? (tl.end_ms - tl.start_ms) : 1000) / 1000 / totalS) * 100 : 0
    return (
      <div>
        <div className="text-[10px] text-slate-400">{label}</div>
        {tl && <div className="text-[10px] font-mono text-slate-400 mt-0.5">{fmt(tl.start_ms)} ~ {fmt(tl.end_ms)}</div>}
        <div className="relative h-3 rounded-full bg-slate-100 overflow-hidden mt-1">
          <div className={cn('absolute top-0 bottom-0 rounded-full opacity-70', tone)} style={{ left: startPct + '%', width: Math.max(widthPct, 2) + '%' }} />
        </div>
      </div>
    )
  }
}

function fmt(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const sec = s % 60
  return m + ':' + String(sec).padStart(2, '0')
}
