import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Asset, Segment } from '../api/types'
import { Icon } from '../lib/icons'

interface Props {
  segment: Segment
  projectId: string
}

/**
 * 分镜资产绑定（折叠式）：默认只展示已绑定标签，「+ 添加资产」展开候选列表按需勾选，
 * 避免资产多时全量罗列过长。角色可多选、场景单选、道具可多选。
 */
export function SegmentAssetBinder({ segment, projectId }: Props) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)

  const { data: assets = [] } = useQuery({
    queryKey: ['assets', projectId],
    queryFn: () => api.get<Asset[]>(`/projects/${projectId}/assets`),
  })

  const bind = useMutation({
    mutationFn: (body: { character_ids?: string[]; scene_id?: string | null; prop_ids?: string[] }) =>
      api.patch<Segment>(`/segments/${segment.id}/assets`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['segments', projectId] }),
  })

  const characters = assets.filter((a) => a.type === 'character')
  const scenes = assets.filter((a) => a.type === 'scene')
  const props = assets.filter((a) => a.type === 'prop')

  const selectedChars = segment.character_ids || []
  const selectedScene = segment.scene_id
  const selectedProps = segment.prop_ids || []

  function toggleCharacter(id: string) {
    const next = selectedChars.includes(id)
      ? selectedChars.filter((c) => c !== id)
      : [...selectedChars, id]
    bind.mutate({ character_ids: next })
  }

  function toggleProp(id: string) {
    const next = selectedProps.includes(id)
      ? selectedProps.filter((p) => p !== id)
      : [...selectedProps, id]
    bind.mutate({ prop_ids: next })
  }

  function selectScene(id: string | null) {
    bind.mutate({ scene_id: id })
  }

  // 已绑定标签
  const boundChars = characters.filter((a) => selectedChars.includes(a.id))
  const boundScene = scenes.find((a) => a.id === selectedScene)
  const boundProps = props.filter((a) => selectedProps.includes(a.id))
  const hasBound = boundChars.length > 0 || !!boundScene || boundProps.length > 0

  // 无任何资产时提示
  if (assets.length === 0) {
    return (
      <div className="bg-slate-50 rounded-lg p-3 mb-2 text-xs text-slate-400 flex items-center gap-2">
        <Icon name="layers" size={14} className="text-zinc-600" />
        暂无资产可用，请先在资产管理创建角色/场景/道具
      </div>
    )
  }

  return (
    <div className="bg-slate-50 rounded-lg p-3 mb-2">
      <div className="flex items-center justify-between mb-2">
        <span className="flex items-center gap-1.5 text-xs text-slate-500">
          <Icon name="layers" size={13} className="text-slate-400" />
          资产绑定
        </span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 transition-colors"
        >
          <Icon name={open ? 'chevron-down' : 'plus'} size={13} />
          {open ? '收起' : '添加资产'}
        </button>
      </div>

      {/* 已绑定摘要（默认折叠态只展示这些） */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {boundChars.map((a) => (
          <span
            key={a.id}
            onClick={() => toggleCharacter(a.id)}
            title="点击移除"
            className="cursor-pointer inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 border border-brand-200 text-xs"
          >
            {a.name}
            <span className="opacity-60">×</span>
          </span>
        ))}
        {boundScene && (
          <span
            onClick={() => selectScene(null)}
            title="点击移除"
            className="cursor-pointer inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-200 text-xs"
          >
            {boundScene.name}
            <span className="opacity-60">×</span>
          </span>
        )}
        {boundProps.map((a) => (
          <span
            key={a.id}
            onClick={() => toggleProp(a.id)}
            title="点击移除"
            className="cursor-pointer inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-orange-500/10 text-orange-600 border border-orange-200 text-xs"
          >
            {a.name}
            <span className="opacity-60">×</span>
          </span>
        ))}
        {!hasBound && (
          <span className="text-xs text-slate-300">未绑定 · 点右侧「添加资产」</span>
        )}
      </div>

      {/* 展开候选列表：按类型分组，点到即绑定 */}
      {open && (
        <div className="space-y-2 mt-2.5 pt-2.5 border-t border-slate-200/70">
          {characters.length > 0 && (
            <div className="flex items-start gap-2">
              <span className="text-xs text-slate-400 flex items-center gap-1 w-10 shrink-0 mt-0.5">
                <Icon name="users" size={13} className="text-slate-400" />
                角色
              </span>
              <div className="flex flex-wrap gap-1.5">
                {characters.map((a) => {
                  const on = selectedChars.includes(a.id)
                  return (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => toggleCharacter(a.id)}
                      disabled={bind.isPending}
                      className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                        on
                          ? 'bg-brand-500 text-white border-brand-500'
                          : 'bg-white text-slate-500 border-slate-200 hover:border-brand-400'
                      }`}
                    >
                      {a.name}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
          {scenes.length > 0 && (
            <div className="flex items-start gap-2">
              <span className="text-xs text-slate-400 flex items-center gap-1 w-10 shrink-0 mt-0.5">
                <Icon name="map-pin" size={13} className="text-slate-400" />
                场景
              </span>
              <div className="flex flex-wrap gap-1.5">
                {scenes.map((a) => {
                  const on = selectedScene === a.id
                  return (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => selectScene(on ? null : a.id)}
                      disabled={bind.isPending}
                      className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                        on
                          ? 'bg-emerald-600 text-white border-emerald-600'
                          : 'bg-white text-slate-500 border-slate-200 hover:border-emerald-400'
                      }`}
                    >
                      {a.name}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
          {props.length > 0 && (
            <div className="flex items-start gap-2">
              <span className="text-xs text-slate-400 flex items-center gap-1 w-10 shrink-0 mt-0.5">
                <Icon name="package" size={13} className="text-slate-400" />
                道具
              </span>
              <div className="flex flex-wrap gap-1.5">
                {props.map((a) => {
                  const on = selectedProps.includes(a.id)
                  return (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => toggleProp(a.id)}
                      disabled={bind.isPending}
                      className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                        on
                          ? 'bg-orange-600 text-white border-orange-600'
                          : 'bg-white text-slate-500 border-slate-200 hover:border-orange-400'
                      }`}
                    >
                      {a.name}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
