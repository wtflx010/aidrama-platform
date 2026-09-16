/**
 * 智能体生成的剧本封面保存到剧本对话框（原「保存到项目」改造，2026-08-24）。
 *
 * 语义变化：AI 生成的封面图不再保存为「项目资产」，而是直接作为所选剧本的
 * 海报（novel.poster_url），供剧本库卡片展示。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, toProxyUrl } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { Icon } from '../../lib/icons'
import type { Novel } from '../../api/types'

export function SaveToNovelDialog({ imageUrl, onClose }: { imageUrl: string; onClose: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [novelId, setNovelId] = useState('')
  const [saving, setSaving] = useState(false)
  const { data: novels = [] } = useQuery<Novel[]>({
    queryKey: ['novels'],
    queryFn: () => api.get<Novel[]>('/novels'),
  })

  const save = async () => {
    if (!novelId || saving) return
    setSaving(true)
    try {
      // 本地 media URL → 走 vite 代理的相对路径（避免跨域 CORS）→ blob → base64（upload-image 接口约定不带 data: 前缀）
      const blob = await (await fetch(toProxyUrl(imageUrl))).blob()
      const b64 = await new Promise<string>((resolve, reject) => {
        const r = new FileReader()
        r.onload = () => resolve(String(r.result).split(',')[1] ?? '')
        r.onerror = reject
        r.readAsDataURL(blob)
      })
      await api.post<Novel>(`/novels/${novelId}/upload-image`, {
        filename: 'poster.png',
        data_base64: b64,
      })
      // 剧本库卡片与创作助手侧栏列表同步刷新
      qc.invalidateQueries({ queryKey: ['novels'] })
      qc.invalidateQueries({ queryKey: ['workbench-novels'] })
      toast.success(`封面已保存到剧本「${novels.find((n) => n.id === novelId)?.title ?? ''}」`)
      onClose()
    } catch (e) {
      toast.error((e as Error).message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[420px] rounded-2xl bg-white shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
            <Icon name="download" size={14} className="text-brand-500" />
            保存封面到剧本
          </span>
          <button type="button" onClick={onClose} className="p-1 rounded text-slate-400 hover:bg-slate-100">
            <Icon name="x" size={15} />
          </button>
        </div>
        <div className="p-4 space-y-3">
          <img src={toProxyUrl(imageUrl)} alt="待保存封面" className="w-full max-h-40 object-cover rounded-lg border border-slate-200" />
          {/* 剧本 */}
          <select value={novelId} onChange={(e) => setNovelId(e.target.value)} className="w-full input-base text-sm">
            <option value="">选择剧本…</option>
            {novels.map((n) => (
              <option key={n.id} value={n.id}>
                {n.title}
              </option>
            ))}
          </select>
          {novels.length === 0 && (
            <p className="text-xs text-slate-400">暂无剧本，请先在智能体对话中生成剧本</p>
          )}
          <Button className="w-full" size="sm" onClick={save} loading={saving} disabled={!novelId}>
            保存到剧本
          </Button>
        </div>
      </div>
    </div>
  )
}
