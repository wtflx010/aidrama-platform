/**
 * 成片评分看板（P0-1 + P0-2）：对一集成片做四维评分（规则+LLM）、查看报告与
 * 逐分镜反哺建议、一键「应用建议」重出弱镜，并展示角色一致性总览。
 * 前端只负责触发任务 + 轮询 + 展示，不碰生成核心。
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  DIMENSIONS,
  evaluateApi,
  scoreColor,
  type EpisodeEvalOut,
  type EvalSuggestion,
} from '../../api/evaluate'
import { useTaskPoller } from '../../api/useTaskPoller'
import type { Episode } from '../../api/types'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { Badge } from '../ui/Badge'
import { Spinner } from '../ui/Spinner'
import { useToast } from '../ui/Toast'
import { Icon } from '../../lib/icons'

interface Props {
  projectId: string
  episodes: Episode[]
  open: boolean
  onClose: () => void
}

export function EvaluatedBoard({ projectId, episodes, open, onClose }: Props) {
  const toast = useToast()
  const qc = useQueryClient()
  const [episodeId, setEpisodeId] = useState<string>('')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [applied, setApplied] = useState(false)

  // 默认选中第一幕
  useEffect(() => {
    if (open && !episodeId && episodes.length > 0) setEpisodeId(episodes[0].id)
  }, [open, episodeId, episodes])

  const task = useTaskPoller(taskId)
  const eid = episodeId || (episodes[0]?.id ?? '')

  const { data: evalOut, isFetching } = useQuery<EpisodeEvalOut | null>({
    queryKey: ['episode-eval', projectId, eid, applied],
    queryFn: () => (eid ? evaluateApi.get(projectId, eid) : null),
    enabled: open && !!eid,
  })

  const { data: consistency } = useQuery({
    queryKey: ['consistency', projectId, eid],
    queryFn: () => (eid ? evaluateApi.consistency(projectId, eid) : null),
    enabled: open && !!eid && evalOut?.status === 'succeeded',
  })

  // 评估任务完成 → 刷新结果
  useEffect(() => {
    if (!task || !taskId) return
    if (task.status === 'succeeded') {
      setApplied((a) => !a) // 变更 queryKey 触发重新拉取
      setTaskId(null)
      toast.success('成片评估完成')
    } else if (task.status === 'failed') {
      toast.error('成片评估失败：' + (task.error || '详情见任务中心'))
      setTaskId(null)
    } else if (task.status === 'cancelled') {
      setTaskId(null)
    }
  }, [task, taskId, toast, qc, projectId, eid])

  function trigger() {
    if (!eid) return
    evaluateApi
      .trigger(projectId, eid)
      .then((r) => {
        setApplied(false)
        setTaskId(r.task_id)
      })
      .catch((e: Error) => toast.error(e.message))
  }

  function apply() {
    if (!eid) return
    evaluateApi
      .applyFeedback(projectId, eid)
      .then((r) => {
        toast.success(r.message || '已应用评估建议（弱镜提示词缓存已清）')
        qc.invalidateQueries({ queryKey: ['project', projectId] })
        setApplied((a) => !a)
      })
      .catch((e: Error) => toast.error(e.message))
  }

  const running = !!task && (task.status === 'pending' || task.status === 'running')
  const suggestions = (evalOut?.suggestions ?? []) as EvalSuggestion[]

  return (
    <Modal open={open} onClose={onClose} title="成片评分看板" size="2xl" footer={
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onClose}>关闭</Button>
      </div>
    }>
      {/* 幕选择 + 触发 */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex items-center gap-2">
          <Icon name="film" size={15} className="text-brand-600" />
          <select
            value={episodeId}
            onChange={(e) => {
              setEpisodeId(e.target.value)
              setTaskId(null)
              setApplied(false)
            }}
            className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40"
          >
            {episodes.map((ep) => (
              <option key={ep.id} value={ep.id}>
                幕 {ep.index + 1} · {ep.title || '未命名'}
              </option>
            ))}
          </select>
        </div>
        <Button variant="primary" size="sm" onClick={trigger} loading={running || isFetching}>
          {running ? '评估中…' : '触发本集评估'}
        </Button>
      </div>

      {!evalOut ? (
        <div className="flex items-center justify-center py-10 text-slate-400 text-sm gap-2">
          <Icon name="film" size={20} /> 该幕暂无评估，点击「触发本集评估」
        </div>
      ) : (
        <div className="space-y-5">
          {/* 四维分 */}
          <div>
            <div className="text-sm font-semibold text-slate-700 mb-2">四维评分（规则 + LLM）</div>
            <div className="grid grid-cols-1 sm:grid-cols-5 gap-3">
              {DIMENSIONS.map((d) => {
                const v = Number(evalOut.scores?.[d.key] ?? 0)
                return (
                  <div key={d.key} className="rounded-xl border border-slate-200 p-3">
                    <div className="flex items-baseline justify-between">
                      <span className="text-xs text-slate-500">{d.label}</span>
                      <span className="text-xl font-bold text-slate-800">{v.toFixed(1)}</span>
                    </div>
                    <div className="mt-2 h-1.5 w-full rounded-full bg-slate-100">
                      <div className={'h-full rounded-full ' + scoreColor(v)} style={{ width: Math.min(100, v * 10) + '%' }} />
                    </div>
                    <div className="mt-1 text-[10px] text-slate-400">{d.hint}</div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* 报告（反哺建议） */}
          {evalOut.report && (
            <div className="rounded-xl border border-brand-500/20 bg-brand-50/50 p-3 text-sm text-slate-600">
              <span className="font-semibold text-brand-700">评估报告：</span>
              {evalOut.report}
            </div>
          )}

          {/* 反哺建议 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="text-sm font-semibold text-slate-700">逐分镜反哺建议（{suggestions.length}）</div>
              <button onClick={apply} className="text-xs text-brand-600 hover:text-brand-700 flex items-center gap-1">
                <Icon name="check-circle" size={12} /> 应用建议并重出弱镜
              </button>
            </div>
            {suggestions.length === 0 ? (
              <div className="text-sm text-slate-400">无（LLM 未给出分镜级建议）</div>
            ) : (
              <ul className="space-y-2">
                {suggestions.map((s, i) => (
                  <li key={i} className="rounded-xl border border-slate-200 p-3">
                    <div className="flex items-start gap-2">
                      <Badge variant="indigo" size="sm">镜 {s.segment_index}</Badge>
                      <div className="min-w-0 text-sm">
                        <div className="font-medium text-slate-700">{s.issue}</div>
                        {s.suggestion && <div className="text-xs text-slate-500 mt-0.5">{s.suggestion}</div>}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* 角色一致性 */}
          <div className="rounded-xl border border-slate-200 p-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-semibold text-slate-700">角色一致性</span>
              {consistency && (
                <Badge variant={Number(consistency.overall_score ?? 0) >= 0.8 ? 'green' : Number(consistency.overall_score ?? 0) >= 0.6 ? 'blue' : 'red'} size="sm">
                  {consistency.overall_score != null ? '一致性 ' + consistency.overall_score.toFixed(2) : '无 retention 数据'}
                </Badge>
              )}
            </div>
            {consistency?.weak_segments?.length ? (
              <div className="text-sm text-rose-600">
                弱保留分镜：镜 {consistency.weak_segments.map((w) => w.segment_index).join('、')}（建议重出）
              </div>
            ) : (
              <div className="text-sm text-slate-400">
                {consistency ? '未发现弱保留分镜（retention_analysis 全部达标）' : '无一致性数据（需先评估/生成视频后消费 H3 retention_analysis）'}
              </div>
            )}
          </div>
        </div>
      )}

      {isFetching && !evalOut && (
        <div className="flex items-center justify-center py-6 gap-2 text-slate-400">
          <Spinner /> 加载中…
        </div>
      )}
    </Modal>
  )
}
