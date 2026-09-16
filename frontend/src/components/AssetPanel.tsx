/**
 * 资产面板：角色 / 场景 / 道具三类美术资产管理。
 * 嵌入项目工作区顶部，支持新建、生成封面/四视图、描述扩写、删除。
 */
import { useEffect, useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, uploadAssetImage } from '../api/client'
import { useTaskPoller } from '../api/useTaskPoller'
import type { Asset, AssetType, AssetGenerateResp, BatchResp, MediaStatus, RecommendVoiceResp, VoiceProfile } from '../api/types'
import { Button } from './ui/Button'
import { Badge } from './ui/Badge'
import { Modal } from './ui/Modal'
import { ProgressBar } from './ui/ProgressBar'
import { EmptyState } from './ui/EmptyState'
import { ZoomableImage } from './ui/ZoomableImage'
import { AudioUploadField } from './ui/AudioUploadField'
import { Icon } from '../lib/icons'
import { useConfirm } from './ui/ConfirmDialog'
import { useToast } from './ui/Toast'

const TYPE_LABELS: Record<AssetType, string> = { character: '角色', scene: '场景', prop: '道具' }
const TYPE_TABS: AssetType[] = ['character', 'scene', 'prop']

type BadgeVariant = 'gray' | 'blue' | 'green' | 'red' | 'purple' | 'orange' | 'indigo' | 'amber'

const STATUS_BADGE: Record<MediaStatus, { text: string; variant: BadgeVariant }> = {
  pending: { text: '排队中', variant: 'gray' },
  running: { text: '生成中', variant: 'blue' },
  succeeded: { text: '已完成', variant: 'green' },
  failed: { text: '失败', variant: 'red' },
}

// P7.9 设定卡方案：four_view_urls = [正面, 侧面, 背面, 特写]（特写取代旧"全身"槽位）
const VIEW_LABELS = ['正面', '侧面', '背面', '特写']

// 图片缓存破坏：cover_url 固定不变，重新生成后浏览器可能命中旧缓存。
// 追加 updated_at 时间戳作为查询参数，确保每次重新生成后展示最新图。
function cacheBust(url: string, updatedAt: string): string {
  if (!url) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}t=${encodeURIComponent(updatedAt)}`
}

const GENDER_LABELS: Record<string, string> = { male: '男', female: '女', neutral: '中性' }
const AGE_LABELS: Record<string, string> = { child: '儿童', youth: '青年', middle: '中年', elder: '老年' }
const EMOTION_OPTIONS = ['平静', '愤怒', '悲伤', '欢快', '紧张', '温馨', '恐惧', '史诗', '冷漠', '震惊']

export function AssetPanel({ projectId }: { projectId: string }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [tab, setTab] = useState<AssetType>('character')
  const [adding, setAdding] = useState(false)
  // P7 批量生成封面：当前 tab 的批量任务轮询
  const [batchTaskId, setBatchTaskId] = useState<string | null>(null)
  const batchTask = useTaskPoller(batchTaskId)

  // 全量资产：用于页签计数（不随 tab 切换变化）
  const { data: allAssets = [] } = useQuery({
    queryKey: ['assets', projectId],
    queryFn: () => api.get<Asset[]>(`/projects/${projectId}/assets`),
  })
  // 当前 tab 的资产列表
  const assets = allAssets.filter((a) => a.type === tab)
  const countByType: Record<AssetType, number> = {
    character: allAssets.filter((a) => a.type === 'character').length,
    scene: allAssets.filter((a) => a.type === 'scene').length,
    prop: allAssets.filter((a) => a.type === 'prop').length,
  }

  const latest = assets[assets.length - 1]
  // 顶层仅做一次终态兜底刷新（每张卡片的 AssetCard 已各自轮询并刷新）
  const latestActiveTask = latest && (latest.status === 'pending' || latest.status === 'running') ? latest.task_id : null
  const latestTask = useTaskPoller(latestActiveTask)
  const latestTaskEnded = latestTask && (latestTask.status === 'succeeded' || latestTask.status === 'failed' || latestTask.status === 'cancelled')
  useEffect(() => {
    if (latestTaskEnded && latest && (latest.status === 'pending' || latest.status === 'running')) {
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
    }
  }, [latestTaskEnded, latest, qc, projectId])

  // 当前 tab 待生成封面的资产数（cover_url 为空）
  const pendingCoverCount = assets.filter((a) => !a.cover_url).length
  // 全项目待生成封面的资产数（角色/场景/道具三类合计）
  const pendingCoverCountAll = allAssets.filter((a) => !a.cover_url).length
  const batchBusy = !!batchTask && (batchTask.status === 'pending' || batchTask.status === 'running')

  // 按当前类型批量生成封面
  const batchCover = useMutation({
    mutationFn: () =>
      api.post<BatchResp>(`/projects/${projectId}/assets/batch-generate-cover?asset_type=${tab}`, {}),
    onSuccess: (r) => {
      setBatchTaskId(r.task.id)
      // 立即刷新资产列表：让每个 AssetCard 拿到子任务 task_id，启动独立轮询，单张图完成即可实时显示
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // 全部类型（角色/场景/道具）批量生成封面：不传 asset_type，后端遍历全项目
  const batchCoverAll = useMutation({
    mutationFn: () =>
      api.post<BatchResp>(`/projects/${projectId}/assets/batch-generate-cover`, {}),
    onSuccess: (r) => {
      setBatchTaskId(r.task.id)
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // 批量任务终态 → 刷新资产列表 + 提示
  useEffect(() => {
    if (!batchTask) return
    if (batchTask.status === 'succeeded' || batchTask.status === 'failed' || batchTask.status === 'cancelled') {
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
      if (batchTask.status === 'succeeded') {
        toast.success(`封面批量生成完成（${batchTask.progress}%）`)
      } else if (batchTask.status === 'failed') {
        toast.error(batchTask.error || '批量生成失败')
      }
    }
  }, [batchTask?.status, projectId, qc, toast])

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 mb-6">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <div className="flex items-center gap-2 shrink-0">
          <Icon name="layers" size={18} className="text-brand-600" />
          <h2 className="text-lg font-semibold text-slate-900">美术资产</h2>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {/* P7 批量生成封面：全部类型（角色/场景/道具）一次性生成 */}
          <Button
            size="sm"
            variant="primary"
            leftIcon={<Icon name="layers" size={14} />}
            disabled={batchBusy || pendingCoverCountAll === 0 || batchCoverAll.isPending}
            loading={batchCoverAll.isPending}
            onClick={() => batchCoverAll.mutate()}
            title={pendingCoverCountAll === 0 ? '全部资产已生成封面' : `为 ${pendingCoverCountAll} 个未生成封面的资产（角色/场景/道具）批量生成`}
          >
            {pendingCoverCountAll > 0 ? `批量生成全部封面（${pendingCoverCountAll}）` : '批量生成全部封面'}
          </Button>
          {/* P7 批量生成封面：仅当前类型 */}
          <Button
            size="sm"
            variant="secondary"
            leftIcon={<Icon name="image" size={14} />}
            disabled={batchBusy || pendingCoverCount === 0 || batchCover.isPending}
            loading={batchCover.isPending}
            onClick={() => batchCover.mutate()}
            title={pendingCoverCount === 0 ? '当前类型资产已全部有封面' : `为 ${pendingCoverCount} 个未生成封面的${TYPE_LABELS[tab]}资产批量生成`}
          >
            {pendingCoverCount > 0 ? `批量${TYPE_LABELS[tab]}封面（${pendingCoverCount}）` : `批量${TYPE_LABELS[tab]}封面`}
          </Button>
          <Button size="sm" leftIcon={<Icon name="plus" size={14} />} onClick={() => setAdding(true)}>
            新建{TYPE_LABELS[tab]}
          </Button>
        </div>
      </div>

      {/* P7 批量生成进度 */}
      {batchBusy && (
        <div className="mb-4">
          <ProgressBar
            value={batchTask!.progress}
            label={batchTask!.status === 'running'
              ? `批量生成封面中 ${batchTask!.progress}%`
              : '批量生成排队中…'}
            variant="brand"
          />
        </div>
      )}

      {/* 类型切换 tab */}
      <div className="flex gap-1 mb-4 border-b border-slate-200">
        {TYPE_TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              tab === t
                ? 'bg-brand-500/10 text-brand-600 border-brand-500 font-medium'
                : 'border-transparent text-slate-500 hover:text-slate-800'
            }`}>
            {TYPE_LABELS[t]}（{countByType[t]}）
          </button>
        ))}
      </div>

      {assets.length === 0 ? (
        <EmptyState icon="layers" title={`还没有${TYPE_LABELS[tab]}资产`} description="点击右上角新建开始创建" />
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {assets.map((a) => (
            <AssetCard key={a.id} asset={a} projectId={projectId} />
          ))}
        </div>
      )}

      <AssetDialog projectId={projectId} type={tab} open={adding}
        onClose={() => setAdding(false)}
        onSaved={() => { setAdding(false); qc.invalidateQueries({ queryKey: ['assets', projectId] }) }}
      />
    </div>
  )
}

function AssetCard({ asset, projectId }: { asset: Asset; projectId: string }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 人工上传封面：隐藏 file input + base64 上传到 upload-image
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)

  const uploadCover = useMutation({
    mutationFn: (file: File) => uploadAssetImage<Asset>(asset.id, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assets', projectId] }),
    onError: (e: Error) => setError(e.message),
  })

  async function handleUploadFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setError(null)
    setUploading(true)
    try {
      await uploadCover.mutateAsync(file)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const pollingTask = (asset.status === 'pending' || asset.status === 'running') ? asset.task_id : null
  const task = useTaskPoller(pollingTask)
  const progress = task?.progress ?? 0

  // 任务终态（生成完成/失败）但资产仍显示进行中 → 资产行是旧缓存，立即刷新列表，
  // 让 cover_url/four_view_urls/status 更新，进度条走完直接显示图片，无需手动刷新。
  const taskEnded = task && (task.status === 'succeeded' || task.status === 'failed' || task.status === 'cancelled')
  const assetActive = asset.status === 'pending' || asset.status === 'running'
  useEffect(() => {
    if (taskEnded && assetActive) {
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
    }
  }, [taskEnded, assetActive, qc, projectId])
  // pending + 无 task_id → "未生成"（资产创建默认 pending，但从未发起生成）
  // pending/running + 有 task_id → 真正在队列/生成中
  const hasTask = !!asset.task_id
  // 任务被用户取消 → 资产不再视为"生成中"：徽章显示已取消，允许重新点击生成
  const taskCancelled = task?.status === 'cancelled'
  const badge = (asset.status === 'pending' && !hasTask)
    ? { text: '未生成', variant: 'gray' as BadgeVariant }
    : taskCancelled
      ? { text: '已取消', variant: 'gray' as BadgeVariant }
      : STATUS_BADGE[asset.status]

  const genCover = useMutation({
    mutationFn: () => api.post<AssetGenerateResp>(`/assets/${asset.id}/generate-cover`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assets', projectId] }),
    onError: (e: Error) => setError(e.message),
  })

  const genFourView = useMutation({
    mutationFn: () => api.post<AssetGenerateResp>(`/assets/${asset.id}/generate-fourview`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assets', projectId] }),
    onError: (e: Error) => setError(e.message),
  })

  const genSceneMultiview = useMutation({
    mutationFn: () => api.post<AssetGenerateResp>(`/assets/${asset.id}/generate-scene-multiview`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assets', projectId] }),
    onError: (e: Error) => setError(e.message),
  })

  const expandDesc = useMutation({
    mutationFn: () => api.post<{ expanded_description: string }>(`/assets/${asset.id}/expand-description`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assets', projectId] }),
    onError: (e: Error) => setError(e.message),
  })

  const del = useMutation({
    mutationFn: () => api.del(`/assets/${asset.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['assets', projectId] }),
  })

  const isCharacter = asset.type === 'character'
  const isScene = asset.type === 'scene'
  // 仅在有活跃 task 时才禁用按钮（pending+无 task_id 表示"未生成"，允许点击）。
  // 任务已终态（成功/失败/取消）→ 不再视为进行中，取消后也能重新点击生成。
  const isBusy = assetActive && hasTask && !taskEnded

  async function handleDelete() {
    if (await confirm({ title: '删除资产', message: '删除该资产？此操作不可撤销。', danger: true })) {
      del.mutate()
    }
  }

  return (
    <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm text-slate-900">{asset.name}</span>
          <Badge variant={badge.variant} dot={isBusy}>{badge.text}</Badge>
        </div>
        <Button variant="ghost" size="sm" leftIcon={<Icon name="trash" size={14} />} onClick={handleDelete}
          className="text-rose-500 hover:text-rose-600">
          删除
        </Button>
      </div>

      {asset.description && <p className="text-xs text-slate-400 mb-2 line-clamp-2">{asset.description}</p>}

      {/* 封面图 */}
      {asset.cover_url ? (
        <ZoomableImage src={cacheBust(asset.cover_url, asset.updated_at)} alt={asset.name}
          className="w-full rounded-lg border border-slate-200 max-h-32 object-contain bg-slate-50 mb-2" />
      ) : isBusy ? (
        <div className="mb-2">
          <ProgressBar value={progress} label={task && task.status === 'running' ? `生成中 ${progress}%` : '排队中…'} variant="brand" />
        </div>
      ) : (
        <div className="w-full h-32 border border-dashed border-slate-200 rounded-lg flex items-center justify-center text-xs text-zinc-600 mb-2">
          无封面
        </div>
      )}

      {asset.status === 'failed' && asset.error && (
        <div className="text-xs text-rose-600 bg-rose-500/10 border border-rose-200 rounded p-1.5 mb-2">{asset.error}</div>
      )}
      {error && <div className="text-xs text-rose-600 mb-2">{error}</div>}

      {/* 四视图：新链路 character_sheet_url（单张四格合一图）优先；旧 four_view_urls 网格兼容 */}
      {isCharacter && (asset.character_sheet_url || asset.four_view_urls.length > 0) && (
        asset.character_sheet_url ? (
          <div className="mb-2">
            <ZoomableImage src={cacheBust(asset.character_sheet_url, asset.updated_at)} alt="四格合一四视图"
              className="w-full rounded border border-slate-200 aspect-[3/2] object-cover bg-slate-50" />
            <p className="text-[10px] text-center text-slate-400 mt-0.5">四格合一（半身特征格+正面/侧面/背面全身）</p>
          </div>
        ) : (
          <div className="grid grid-cols-4 gap-1 mb-2">
            {asset.four_view_urls.map((url, i) => (
              <div key={i}>
                <ZoomableImage src={cacheBust(url, asset.updated_at)} alt={VIEW_LABELS[i]}
                  className="w-full rounded border border-slate-200 aspect-square object-cover bg-slate-50" />
                <p className="text-[10px] text-center text-slate-400 mt-0.5">{VIEW_LABELS[i]}</p>
              </div>
            ))}
          </div>
        )
      )}

      {/* 场景多视角六格合一图（仅 scene 类型） */}
      {isScene && asset.scene_sheet_url && (
        <div className="mb-2">
          <ZoomableImage src={cacheBust(asset.scene_sheet_url, asset.updated_at)} alt="场景多视角六格合一图"
            className="w-full rounded border border-slate-200 object-cover bg-slate-50" />
          <p className="text-[10px] text-center text-slate-400 mt-0.5">六格合一·同一场景六机位（场景以封面为基准，仅改变相机位置）</p>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex flex-wrap gap-1.5">
        <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.webp"
          onChange={handleUploadFile} className="hidden" />
        <Button size="sm" variant="primary" leftIcon={<Icon name="image" size={14} />}
          disabled={isBusy || busy}
          onClick={() => { setError(null); setBusy(true); genCover.mutateAsync().finally(() => setBusy(false)) }}>
          {asset.cover_url ? '重生封面' : '生成封面'}
        </Button>
        <Button size="sm" variant="outline" leftIcon={<Icon name="upload" size={14} />}
          disabled={uploading}
          loading={uploading}
          onClick={() => fileRef.current?.click()}
          title="上传本地图片作为该资产封面/主图（替换 AI 生成）">
          {asset.cover_url ? '替换封面' : '上传封面'}
        </Button>
        {isCharacter && (
          <Button size="sm" variant="secondary" leftIcon={<Icon name="grid" size={14} />}
            disabled={isBusy || !asset.cover_url}
            onClick={() => { setError(null); genFourView.mutate() }}
            title={!asset.cover_url ? '需先生成封面' : ''}>
            {asset.character_sheet_url || asset.four_view_urls.length > 0 ? '重生四视图' : '生成四视图'}
          </Button>
        )}
        {isScene && (
          <Button size="sm" variant="secondary" leftIcon={<Icon name="grid" size={14} />}
            disabled={isBusy || !asset.cover_url}
            loading={genSceneMultiview.isPending}
            onClick={() => { setError(null); genSceneMultiview.mutate() }}
            title={!asset.cover_url ? '需先生成封面' : '生成场景多视角（约8-15分钟）'}>
            {asset.scene_sheet_url ? '重生多视角' : '生成多视角'}
          </Button>
        )}
        <Button size="sm" variant="outline" leftIcon={<Icon name="wand" size={14} />}
          disabled={isBusy}
          onClick={() => { setError(null); expandDesc.mutate() }}>
          扩写描述
        </Button>
      </div>
      <PromptEditor asset={asset} projectId={projectId} onError={setError} />

      {/* 场景多视角机位组（POV 六格，2026-08-18 v5） */}
      {isScene && <ShotEditor asset={asset} projectId={projectId} onError={setError} />}

      {/* P2 角色声线档案 */}
      {isCharacter && (
        <VoiceProfileSection asset={asset} projectId={projectId} onError={setError} />
      )}
    </div>
  )
}

/** 生图提示词（扩写描述）展示 + 编辑：系统生成的 prompt 支持人工修正后重新生成。 */
function PromptEditor({ asset, projectId, onError }: {
  asset: Asset
  projectId: string
  onError: (msg: string | null) => void
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)

  const save = useMutation({
    mutationFn: (v: string) =>
      api.put<Asset>(`/assets/${asset.id}`, { expanded_description: v }),
    onSuccess: () => {
      onError(null)
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
      setEditing(false)
    },
    onError: (e: Error) => onError(e.message),
  })

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between">
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1 cursor-pointer"
        >
          <Icon name="type" size={12} />
          生图提示词
          <Icon name="chevron-down" size={10} />
        </button>
        {open && (
          <div className="flex gap-1">
            {editing ? (
              <>
                <Button size="sm" variant="ghost" onClick={() => { setValue(asset.expanded_description || ''); setEditing(false) }}>
                  取消
                </Button>
                <Button size="sm" variant="primary" loading={saving}
                  onClick={() => { setSaving(true); save.mutate(value, { onSettled: () => setSaving(false) }) }}>
                  保存
                </Button>
              </>
            ) : (
              <Button size="sm" variant="ghost" leftIcon={<Icon name="pencil" size={11} />}
                onClick={() => { setValue(asset.expanded_description || ''); setEditing(true) }}>
                编辑
              </Button>
            )}
          </div>
        )}
      </div>
      {open && (
        editing ? (
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            rows={5}
            className="input-base w-full mt-1.5 resize-none text-xs leading-relaxed font-mono"
            placeholder="生图提示词（英文），保存后供封面/四视图/关键帧/视频复用"
          />
        ) : asset.expanded_description ? (
          <p className="text-xs text-slate-400 mt-1.5 whitespace-pre-wrap bg-slate-50/50 rounded-lg p-2 border border-slate-200">
            {asset.expanded_description}
          </p>
        ) : (
          <p className="text-xs text-slate-400 mt-1.5 bg-slate-50/50 rounded-lg p-2 border border-slate-200">
            尚未生成提示词。点击「扩写描述」自动生成，或点「编辑」手动填写。
          </p>
        )
      )}
    </div>
  )
}

/** 场景多视角机位组编辑（POV 六格，2026-08-18 v5）。
 * JSON 格式：list[{"name","view_text"}]；留空 → 生成时依次用 导演LLM推断 / 内置默认组。 */
function ShotEditor({ asset, projectId, onError }: {
  asset: Asset
  projectId: string
  onError: (msg: string | null) => void
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)

  const shots: Array<{ name?: string; view_text?: string }> = asset.scene_shots || []

  const save = useMutation({
    mutationFn: (v: string) => {
      const parsed = JSON.parse(v) // 非法 JSON → 抛错由 onError 展示
      if (!Array.isArray(parsed)) throw new Error('机位组必须是列表，如 [{"name":"…","view_text":"…"}]')
      return api.put<Asset>(`/assets/${asset.id}`, { scene_shots: parsed })
    },
    onSuccess: () => {
      onError(null)
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
      setEditing(false)
    },
    onError: (e: Error) => onError(e.message),
  })

  const clear = useMutation({
    mutationFn: () => api.put<Asset>(`/assets/${asset.id}`, { scene_shots: [] }),
    onSuccess: () => {
      onError(null)
      qc.invalidateQueries({ queryKey: ['assets', projectId] })
      setEditing(false)
    },
    onError: (e: Error) => onError(e.message),
  })

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between">
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1 cursor-pointer"
        >
          <Icon name="grid" size={12} />
          多视角机位组（POV）
          <Icon name="chevron-down" size={10} />
        </button>
        {open && (
          <div className="flex gap-1">
            {editing ? (
              <>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>取消</Button>
                <Button size="sm" variant="primary" loading={saving}
                  onClick={() => { setSaving(true); save.mutate(value, { onSettled: () => setSaving(false) }) }}>
                  保存
                </Button>
              </>
            ) : (
              <>
                <Button size="sm" variant="ghost" leftIcon={<Icon name="pencil" size={11} />}
                  onClick={() => { setValue(JSON.stringify(shots, null, 2)); setEditing(true) }}>
                  编辑
                </Button>
                {shots.length > 0 && (
                  <Button size="sm" variant="ghost" leftIcon={<Icon name="trash" size={11} />}
                    onClick={() => clear.mutate()}>
                    清空
                  </Button>
                )}
              </>
            )}
          </div>
        )}
      </div>
      {open && (
        editing ? (
          <div className="mt-1.5">
            <textarea
              value={value}
              onChange={(e) => setValue(e.target.value)}
              rows={8}
              className="input-base w-full resize-none text-xs leading-relaxed font-mono"
              placeholder='[{"name":"entrance_eye","view_text":"Panel 1 (top-left): …"}, …]&#10;留空 [] = 使用 导演LLM按剧本推断 或 内置默认机位组'
            />
            <p className="text-[11px] text-slate-400 mt-1">
              六格结构建议：俯视top / 正面front / 回望lookback / 左侧left / 右侧right / 远景wide，每格 = 人物站位 + 朝向 + 机位参数（高度/俯仰/焦距/景别），视角两两不重复。
            </p>
          </div>
        ) : shots.length > 0 ? (
          <ul className="text-xs text-slate-500 mt-1.5 space-y-1">
            {shots.map((s, i) => (
              <li key={i} className="flex gap-2">
                <span className="shrink-0 font-mono text-slate-400">[{i + 1}]</span>
                <span className="truncate"><b>{s.name}</b> {s.view_text}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-slate-400 mt-1.5 bg-slate-50/50 rounded-lg p-2 border border-slate-200">
            未自定义机位组。生成时自动使用：剧本镜头推断（有分镜时）或内置默认机位组（室内/室外各 6 格）。
          </p>
        )
      )}
    </div>
  )
}

/** 角色声线档案区块：推荐 + 编辑 + 参考音频试听。 */
function VoiceProfileSection({
  asset,
  projectId,
  onError,
}: {
  asset: Asset
  projectId: string
  onError: (msg: string | null) => void
}) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [recommending, setRecommending] = useState(false)
  const vp: VoiceProfile = asset.voice_profile || {
    gender: null, age_group: null, timbre_tags: [], reference_audio_url: null,
    reference_audio_text: null, default_emotion: null, voice_description: null,
  }

  const hasProfile = !!(
    vp.gender || vp.age_group || vp.timbre_tags?.length || vp.default_emotion || vp.voice_description
  )

  const recommend = useMutation({
    mutationFn: () => api.post<RecommendVoiceResp>(`/assets/${asset.id}/recommend-voice`, {}),
    onSuccess: () => { onError(null); qc.invalidateQueries({ queryKey: ['assets', projectId] }) },
    onError: (e: Error) => onError(e.message),
  })

  async function handleRecommend() {
    setRecommending(true); onError(null)
    try { await recommend.mutateAsync() } catch { /* onError 已处理 */ } finally { setRecommending(false) }
  }

  return (
    <div className="mt-2 border-t border-slate-200 pt-2">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-medium text-slate-600 flex items-center gap-1.5">
          <Icon name="mic" size={12} />
          声线档案
        </span>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" leftIcon={<Icon name="wand" size={12} />}
            disabled={recommending} loading={recommending} onClick={handleRecommend}
            title="LLM 根据角色设定推荐声线">
            推荐声线
          </Button>
          <Button size="sm" variant="ghost" leftIcon={<Icon name="pencil" size={12} />}
            onClick={() => setEditing(true)}>
            编辑
          </Button>
        </div>
      </div>

      {hasProfile ? (
        <div className="bg-white/60 rounded-lg border border-slate-200 p-2 space-y-1">
          <div className="flex flex-wrap gap-1">
            {vp.gender && <Badge variant="blue" size="sm">{GENDER_LABELS[vp.gender] ?? vp.gender}</Badge>}
            {vp.age_group && <Badge variant="gray" size="sm">{AGE_LABELS[vp.age_group] ?? vp.age_group}</Badge>}
            {vp.default_emotion && <Badge variant="purple" size="sm">默认·{vp.default_emotion}</Badge>}
            {vp.timbre_tags?.map((t) => (
              <Badge key={t} variant="indigo" size="sm">{t}</Badge>
            ))}
          </div>
          {vp.voice_description && (
            <p className="text-[11px] text-slate-500 italic">{vp.voice_description}</p>
          )}
          {vp.reference_audio_url && (
            <div className="space-y-1">
              <p className="text-[10px] text-slate-400">参考音频（CosyVoice zero_shot）</p>
              <audio src={vp.reference_audio_url} controls className="w-full h-7" />
              {vp.reference_audio_text && (
                <p className="text-[10px] text-slate-400">参考文本：{vp.reference_audio_text}</p>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="text-[11px] text-slate-400">
          未配置声线档案。点击「推荐声线」由 LLM 根据角色设定生成，或「编辑」手动填写。
        </p>
      )}

      {editing && (
        <VoiceProfileDialog
          asset={asset}
          onClose={() => setEditing(false)}
          onSaved={() => { setEditing(false); qc.invalidateQueries({ queryKey: ['assets', projectId] }) }}
          onError={onError}
        />
      )}
    </div>
  )
}

/** 声线档案编辑弹窗。 */
function VoiceProfileDialog({
  asset,
  onClose,
  onSaved,
  onError,
}: {
  asset: Asset
  onClose: () => void
  onSaved: () => void
  onError: (msg: string | null) => void
}) {
  const initial: VoiceProfile = asset.voice_profile || {
    gender: null, age_group: null, timbre_tags: [], reference_audio_url: null,
    reference_audio_text: null, default_emotion: null, voice_description: null,
  }
  const [gender, setGender] = useState(initial.gender || '')
  const [ageGroup, setAgeGroup] = useState(initial.age_group || '')
  const [timbreTags, setTimbreTags] = useState((initial.timbre_tags || []).join('，'))
  const [defaultEmotion, setDefaultEmotion] = useState(initial.default_emotion || '')
  const [referenceAudioUrl, setReferenceAudioUrl] = useState(initial.reference_audio_url || '')
  const [referenceAudioText, setReferenceAudioText] = useState(initial.reference_audio_text || '')
  const [voiceDescription, setVoiceDescription] = useState(initial.voice_description || '')
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true); onError(null)
    try {
      const body: Partial<VoiceProfile> = {
        gender: gender || null,
        age_group: ageGroup || null,
        timbre_tags: timbreTags ? timbreTags.split(/[,，]/).map((s) => s.trim()).filter(Boolean) : [],
        default_emotion: defaultEmotion || null,
        reference_audio_url: referenceAudioUrl || null,
        reference_audio_text: referenceAudioText || null,
        voice_description: voiceDescription || null,
      }
      await api.put(`/assets/${asset.id}/voice-profile`, body)
      onSaved()
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={`声线档案 · ${asset.name}`} size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button variant="primary" size="sm" loading={saving} onClick={save}>保存</Button>
        </>
      }>
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-slate-500 mb-1 block">性别</label>
            <select value={gender} onChange={(e) => setGender(e.target.value)} className="input-base">
              <option value="">不选</option>
              <option value="male">男</option>
              <option value="female">女</option>
              <option value="neutral">中性</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1 block">年龄段</label>
            <select value={ageGroup} onChange={(e) => setAgeGroup(e.target.value)} className="input-base">
              <option value="">不选</option>
              <option value="child">儿童</option>
              <option value="youth">青年</option>
              <option value="middle">中年</option>
              <option value="elder">老年</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1 block">默认情绪</label>
            <select value={defaultEmotion} onChange={(e) => setDefaultEmotion(e.target.value)} className="input-base">
              <option value="">不选</option>
              {EMOTION_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className="text-xs text-slate-500 mb-1 block">音色标签（逗号分隔）</label>
          <input value={timbreTags} onChange={(e) => setTimbreTags(e.target.value)}
            className="input-base w-full" placeholder="如：低沉，温和，沙哑" />
        </div>
        <AudioUploadField
          url={referenceAudioUrl}
          onUrlChange={setReferenceAudioUrl}
          referenceText={referenceAudioText}
          onReferenceTextChange={setReferenceAudioText}
        />
        <div>
          <label className="text-xs text-slate-500 mb-1 block">声线描述</label>
          <textarea value={voiceDescription} onChange={(e) => setVoiceDescription(e.target.value)}
            rows={2} className="input-base w-full resize-none"
            placeholder="声线整体描述，供展示与再推荐" />
        </div>
      </div>
    </Modal>
  )
}

function AssetDialog({ projectId, type, open, onClose, onSaved }: {
  projectId: string
  type: AssetType
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    if (!name.trim()) { setError('请填写名称'); return }
    setLoading(true); setError(null)
    try {
      await api.post(`/projects/${projectId}/assets`, { type, name: name.trim(), description: description || null })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={`新建${TYPE_LABELS[type]}`} size="sm"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button variant="primary" size="sm" loading={loading} onClick={save}>创建</Button>
        </>
      }>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-slate-500">名称</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="input-base w-full mt-1" placeholder="如：小雨" />
        </div>
        <div>
          <label className="text-xs text-slate-500">描述</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
            className="input-base w-full mt-1 resize-none"
            placeholder={
              type === 'character'
                ? '纯外观描述：外貌/发型/服饰/体态/气质（⚠️ 不要写动作或场景，如…蹲下/坐下/端着茶杯，否则动作会被画进封面）'
                : type === 'scene'
                  ? '纯环境描述：空间/布局/光线/氛围（⚠️ 不要写人物或动作，如…一家人/辅导作业，否则人会被混进画面）'
                  : '纯粹道具描述：造型/材质/纹饰/特征（⚠️ 不要写人物或手持，如…端着的/手持，否则手会被画进画面）'
            } />
        </div>
      </div>
      {error && <p className="text-sm text-rose-500 mt-2">{error}</p>}
    </Modal>
  )
}