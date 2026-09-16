/** 分镜工作台 主入口（三列：左分镜 / 中视频 / 右详情）。
 *  批量生成分镜 / 合成成片的操作入口已上移到项目详情页顶栏（追加章节右侧）。 */
import { useState, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Episode, Project, Segment, VideoClip } from '../api/types'
import { StudioLeftColumn } from './studio/StudioLeftColumn'
import { StudioMiddleColumn } from './studio/StudioMiddleColumn'
import { RightPanel } from './studio/StudioRightPanel'

interface TimelineShot { segment_id: string; episode_index: number; segment_index: number; title: string | null; start_ms: number; end_ms: number; duration: number }
interface TimelineData { total_duration_s: number; project_timeline: TimelineShot[]; script_timeline: TimelineShot[] }

export function ShotStudio({ project, episodes, segments, projectId, showBatch, setShowBatch, batchActive = false }: {
  project: Project
  episodes: Episode[]
  segments: Segment[]
  projectId: string
  showBatch: boolean
  setShowBatch: (v: boolean) => void
  /** 批量生成视频进行中：分镜视频列表自动轮询刷新，生成完成无需手动刷新页面 */
  batchActive?: boolean
}) {
  const [collapsedEps, setCollapsedEps] = useState<Set<string>>(new Set())
  // 画布「在分镜编辑器中打开」跳转 /projects/:id?shot=<segmentId> → 用该参数定位分镜
  const [searchParams] = useSearchParams()
  const shotParam = searchParams.get('shot')
  const [selectedId, setSelectedId] = useState<string | null>(shotParam || null)
  const [centerTab, setCenterTab] = useState<'shot' | 'film'>('shot')

  const selected = segments.find((s) => s.id === selectedId) || null
  const epById = useMemo(() => new Map(episodes.map((e) => [e.id, e])), [episodes])

  useEffect(() => {
    if (selectedId && !segments.some((s) => s.id === selectedId)) {
      // 画布跳转的目标分镜不存在（已删除/切换项目）→ 回退第一镜
      setSelectedId(segments.length > 0 ? segments[0].id : null)
    } else if (!selectedId && segments.length > 0) {
      setSelectedId(segments[0].id)
    }
  }, [segments, selectedId])

  // 全部分镜视频（惰性拉取）
  const { data: allClipMap = {} as Record<string, VideoClip[]> } = useQuery({
    queryKey: ['studio-clips', projectId],
    queryFn: async () => {
      const out: Record<string, VideoClip[]> = {}
      for (const s of segments) {
        try { out[s.id] = await api.get<VideoClip[]>(('/segments/' + s.id + '/videos')) }
        catch { out[s.id] = [] }
      }
      return out
    },
    enabled: segments.length > 0,
    // 批量生成期间每 5s 自动刷新分镜视频，出片即见（无需手动刷新页面）
    refetchInterval: batchActive ? 5000 : false,
  })

  const { data: timeline } = useQuery({
    queryKey: ['timeline', projectId],
    queryFn: () => api.get<TimelineData>(('/projects/' + projectId + '/timeline')),
  })

  return (
    <div className="flex h-full bg-white p-4 gap-4">
      <StudioLeftColumn
        episodes={episodes}
        segments={segments}
        collapsedEps={collapsedEps}
        toggleEp={(id) => setCollapsedEps((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })}
        selectedId={selectedId}
        onSelect={setSelectedId}
        clips={allClipMap}
      />
      <StudioMiddleColumn
        project={project}
        selected={selected}
        segments={segments}
        epById={epById}
        centerTab={centerTab}
        setCenterTab={setCenterTab}
        onSelect={setSelectedId}
        allClipMap={allClipMap}
        projectId={projectId}
        showBatch={showBatch}
        setShowBatch={setShowBatch}
      />
      <RightPanel
        key={selected ? selected.id : 'none'}
        segment={selected}
        timeline={timeline}
        projectId={projectId}
        project={project}
        allClipMap={allClipMap}
      />
    </div>
  )
}
