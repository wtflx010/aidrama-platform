/**
 * 资产库（顶层页签）：全局美术资产，跨项目可复用。
 *
 * - GET /assets/library → 全部资产（项目删除仅解绑，资产保留在库）
 * - 资产与项目的绑定由剧本改编时自动建立，页面只读展示绑定的项目，不提供手动绑定
 * - 点击资产缩略图进入资产详情页
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ArtStyle, Asset, Project } from '../api/types'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import { useToast } from '../components/ui/Toast'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

const TYPE_LABEL: Record<string, { text: string; cls: string }> = {
  character: { text: '角色', cls: 'bg-brand-500/10 text-brand-600 border-brand-200' },
  scene: { text: '场景', cls: 'bg-emerald-500/10 text-emerald-600 border-emerald-200' },
  prop: { text: '道具', cls: 'bg-orange-500/10 text-orange-600 border-orange-200' },
}

const TYPE_FILTERS = [
  { value: '', label: '全部' },
  { value: 'character', label: '角色' },
  { value: 'scene', label: '场景' },
  { value: 'prop', label: '道具' },
]

// 2026-08-30 绑定项目也可删除；原 isBound 绑定保护已移除

export default function AssetLibraryPage() {
  const [typeFilter, setTypeFilter] = useState('')
  const [styleFilter, setStyleFilter] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [selMode, setSelMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [delAllBusy, setDelAllBusy] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()

  const { data: assets = [], isLoading } = useQuery({
    queryKey: ['asset-library', typeFilter],
    queryFn: () => api.get<Asset[]>(typeFilter ? '/assets/library?type=' + typeFilter : '/assets/library'),
  })
  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get<Project[]>('/projects'),
  })
  const { data: styles = [] } = useQuery({
    queryKey: ['art-styles'],
    queryFn: () => api.get<ArtStyle[]>('/art-styles'),
  })

  // 方案A：风格指纹 → 筛选 key / 展示标签
  function styleKey(a: Asset): string {
    const fp = a.style_fingerprint as { style_id?: string | null; style_prompt?: string | null } | null | undefined
    const id = fp?.style_id || ''
    const p = (fp?.style_prompt || '').trim()
    return id ? 'id:' + id : p ? 'prompt:' + p : ''
  }
  function styleLabel(a: Asset): string {
    const fp = a.style_fingerprint as { style_id?: string | null; style_prompt?: string | null } | null | undefined
    if (!fp || (!fp.style_id && !fp.style_prompt)) return '未标注'
    const st = styles.find((s) => s.id === fp.style_id)
    if (st) return st.name
    const p = (fp.style_prompt || '').trim()
    return p ? (p.length > 14 ? p.slice(0, 14) + '…' : p) : '未标注'
  }
  const styleKeys = Array.from(new Set(assets.map(styleKey).filter(Boolean)))
  const visibleAssets = styleFilter ? assets.filter((a) => styleKey(a) === styleFilter) : assets
  // 绑定保护：未绑定任何项目的资产才允许删除
  // 2026-08-30 绑定项目也可删除：一键删除全部始终可用

  async function deleteAsset(a: Asset) {
    if (deletingId) return
    if (!window.confirm('删除资产「' + a.name + '」？封面/四视图/多视角等文件将一并删除，不可恢复。')) return
    setDeletingId(a.id)
    try {
      await api.del('/assets/' + a.id)
      qc.invalidateQueries({ queryKey: ['asset-library'] })
      toast.success('资产已删除')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDeletingId(null)
    }
  }

  function toggleSel(id: string) {
    // 绑定项目也可删除（2026-08-30）
    setSelected((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  async function batchDelete() {
    const unbound = Array.from(selected).filter((id) => visibleAssets.find((x) => x.id === id))
    if (unbound.length === 0) return
    if (!window.confirm('删除选中的 ' + unbound.length + ' 个资产？封面/四视图/多视角等文件将一并删除，不可恢复。')) return
    setDeletingId('__batch__')
    try {
      const r = await api.post<{ deleted: number; protected: string[] }>('/assets/batch-delete', { asset_ids: unbound })
      qc.invalidateQueries({ queryKey: ['asset-library'] })
      toast.success('已删除 ' + (r.deleted ?? 0) + ' 个资产')
      setSelected(new Set())
      setSelMode(false)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDeletingId(null)
    }
  }

  async function deleteAll() {
    if (!window.confirm('确定删除【全部资产】？将删除所有资产（含绑定项目的），并清理其文件，不可恢复。')) return
    setDelAllBusy(true)
    try {
      const r = await api.post<{ deleted: number; protected: string[] }>('/assets/delete-all')
      qc.invalidateQueries({ queryKey: ['asset-library'] })
      toast.success('已删除全部 ' + (r.deleted ?? 0) + ' 个资产')
      setSelected(new Set())
      setSelMode(false)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDelAllBusy(false)
    }
  }

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8 animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
            <span className="w-9 h-9 rounded-xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center">
              <Icon name="layers" size={20} className="text-brand-600" />
            </span>
            美术
          </h1>
          <p className="text-sm text-slate-400 mt-1.5 ml-12">
            全局美术资产（角色/场景/道具）· 绑定由剧本改编自动建立 · 项目删除时随资产归属项目一并清理
          </p>
        </div>
      </div>

      {/* 类型筛选 */}
      <div className="flex items-center gap-1.5 mb-5">
        {TYPE_FILTERS.map((t) => (
          <button
            key={t.value}
            onClick={() => setTypeFilter(t.value)}
            className={cn(
              'px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors',
              typeFilter === t.value
                ? 'bg-brand-500 text-white border-brand-500'
                : 'bg-white text-slate-600 border-slate-200 hover:border-brand-400',
            )}
          >
            {t.label}
          </button>
        ))}
        <select
          value={styleFilter}
          onChange={(e) => setStyleFilter(e.target.value)}
          className="input-base text-xs py-1.5 w-44 ml-2"
          title="按生成风格筛选（方案A 风格指纹）"
        >
          <option value="">全部风格</option>
          {styleKeys.map((k) => {
            const sample = assets.find((x) => styleKey(x) === k)
            return <option key={k} value={k}>{sample ? styleLabel(sample) : k}</option>
          })}
        </select>
        <Button size="sm" variant={selMode ? 'secondary' : 'outline'} onClick={() => setSelMode((v) => !v)} leftIcon={<Icon name="list" size={13} />}>
          {selMode ? '退出批量' : '批量选择'}
        </Button>
        <Button
          size="sm"
          variant="danger"
          loading={delAllBusy}
          disabled={delAllBusy}
          onClick={deleteAll}
          leftIcon={<Icon name="trash" size={13} />}
          title="删除全部资产（含绑定项目的）"
        >
          一键删除全部
        </Button>
        <span className="text-xs text-slate-400 ml-auto">{visibleAssets.length} 个资产</span>
      </div>

      {selMode && (
        <div className="flex items-center gap-2.5 mb-4 px-3 py-2 rounded-lg bg-brand-500/[0.06] border border-brand-200 text-xs">
          <span className="text-slate-600 shrink-0">已选 {selected.size} 项</span>
          <button className="text-brand-600 hover:underline" onClick={() => setSelected(new Set(visibleAssets.map((a) => a.id)))}>全选当前</button>
          <button className="text-slate-500 hover:underline" onClick={() => setSelected(new Set())}>清空</button>
          <span className="text-slate-400 shrink-0">已选 {selected.size} 项资产均可删除</span>
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => { setSelMode(false); setSelected(new Set()) }}>退出</Button>
            <Button size="sm" variant="danger" disabled={selected.size === 0} loading={deletingId === '__batch__'} onClick={batchDelete} leftIcon={<Icon name="trash" size={12} />}>
              删除所选（{selected.size}）
            </Button>
          </div>
        </div>
      )}
      {isLoading ? (
        <div className="text-sm text-slate-400 py-10 text-center">加载中…</div>
      ) : assets.length === 0 ? (
        <EmptyState icon="layers" title="美术库为空" description="在任一项目「美术资产」页签创建资产；项目删除时随项目一并清理" />
      ) : visibleAssets.length === 0 && styleFilter ? (
        <div className="text-sm text-slate-400 py-10 text-center">该风格下暂无资产</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {visibleAssets.map((a) => {
            const tl = TYPE_LABEL[a.type] ?? { text: a.type, cls: 'bg-slate-500/10 text-slate-500 border-slate-200' }
            return (
              <div key={a.id} className="relative rounded-xl border border-slate-200 bg-white overflow-hidden shadow-sm flex flex-col group">
                {/* 删除资产（悬停显示）；绑定项目也可删除（2026-08-30） */}
                <button
                  onClick={() => deleteAsset(a)}
                  disabled={deletingId === a.id}
                  title="删除资产"
                  className="absolute top-2 right-2 z-10 w-6 h-6 rounded-full bg-white/90 border border-slate-200 text-slate-400 hover:text-rose-500 hover:border-rose-300 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-40"
                >
                  <Icon name="trash" size={13} />
                </button>
                {/* 批量选择框（绑定项目也可选/删，2026-08-30） */}
                {selMode && (
                  <button
                    onClick={(e) => { e.stopPropagation(); toggleSel(a.id) }}
                    className={cn('absolute top-2 left-2 z-10 w-5 h-5 rounded-md border flex items-center justify-center bg-white transition-colors',
                      selected.has(a.id) ? 'bg-brand-500 border-brand-500 text-white' : 'border-slate-300 text-slate-400')}
                  >
                    <Icon name="check" size={12} />
                  </button>
                )}

                {/* 缩略图 + 信息（点击缩略图进入资产详情） */}
                <div className="flex items-start gap-3 p-3">
                  <Link to={'/assets/' + a.id} className="w-24 h-24 rounded-lg overflow-hidden bg-slate-100 shrink-0">
                    {a.cover_url ? (
                      <img src={a.cover_url} alt={a.name} className="w-full h-full object-cover transition-transform group-hover:scale-105" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center">
                        <Icon name="image" size={22} className="text-slate-300" />
                      </div>
                    )}
                  </Link>
                  <div className="min-w-0 flex-1 flex flex-col gap-1.5">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-medium border shrink-0', tl.cls)}>{tl.text}</span>
                      <span className="font-semibold text-slate-800 text-sm truncate">{a.name}</span>
                      <span className="px-1.5 py-0.5 rounded text-[10px] border shrink-0 bg-violet-50 text-violet-600 border-violet-200">{styleLabel(a)}</span>
                    </div>
                    <p className="text-xs text-slate-400 line-clamp-2">{a.description}</p>
                    {/* 绑定项目（只读展示，剧本改编时自动绑定） */}
                    <div className="flex flex-wrap gap-1 items-center">
                      <Icon name="folder" size={11} className="text-slate-300" />
                      {a.project_ids.length > 0 ? (
                        a.project_ids.slice(0, 3).map((pid) => (
                          <span
                            key={pid}
                            className="inline-flex items-center text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500"
                          >
                            {projects.find((p) => p.id === pid)?.title || pid.slice(0, 8)}
                          </span>
                        ))
                      ) : (
                        <span className="text-[11px] text-slate-300">未绑定项目</span>
                      )}
                      {a.project_ids.length > 3 && (
                        <span className="text-[11px] text-slate-400">+{a.project_ids.length - 3}</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}