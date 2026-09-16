/**
 * P6 章节续接追加弹窗：在项目详情页选择来源剧本章节范围，追加生成新幕。
 *
 * 流程：
 * 1. 展示来源剧本 + 章节列表（已追加断点标记）
 * 2. （可选）导入后续章节：内容更新后把新章节合并进剧本，继续追加生成
 * 3. 输入起始/结束章号（默认从断点 +1 起，默认 3 章一批）
 * 4. 确认 → POST /novels/{novelId}/adapt-continuation → 轮询任务
 * 5. 成功 → 刷新幕列表/分镜列表
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useTaskPoller } from '../api/useTaskPoller'
import type { NovelChapter, NovelDetail } from '../api/types'
import { Button } from './ui/Button'
import { Badge } from './ui/Badge'
import { Modal } from './ui/Modal'
import { ProgressBar } from './ui/ProgressBar'
import { useToast } from './ui/Toast'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

/** 每批默认追加的章节数（长章节建议 ≤3 章/批） */
const DEFAULT_BATCH = 3

export function ContinuationDialog({
  novelId,
  projectId,
  onClose,
  onSaved,
}: {
  novelId: string
  projectId: string
  onClose: () => void
  onSaved: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()

  const { data: novel } = useQuery({
    queryKey: ['novel', novelId],
    queryFn: () => api.get<NovelDetail>(`/novels/${novelId}`),
  })
  const { data: chapters = [] } = useQuery({
    queryKey: ['novel-chapters', novelId, projectId],
    queryFn: () => api.get<NovelChapter[]>(`/novels/${novelId}/chapters?project_id=${projectId}`),
  })

  // 断点 = 最后一个 processed 章节的 index（0 = 尚未追加）
  const upto = chapters.reduce((acc, c) => (c.processed ? c.index : acc), 0)
  const total = chapters.length
  const [start, setStart] = useState(upto + 1)
  const [end, setEnd] = useState(Math.min(upto + DEFAULT_BATCH, Math.max(upto + 1, total)))
  const [taskId, setTaskId] = useState<string | null>(null)
  const [appendText, setAppendText] = useState('')
  // 防重复刷新（StrictMode / 多次 effect 触发保护）
  const savedRef = useRef(false)

  const task = useTaskPoller(taskId)
  const busy = !!task && (task.status === 'pending' || task.status === 'running')

  // 追加前置依赖：续接改编需要先完成剧本分析（整本分章摘要 + 剧情要素，供续接保持一致）。
  // 未分析/分析失败时弹窗内可直接触发分析，完成后自动解锁追加。
  const [analyzeTaskId, setAnalyzeTaskId] = useState<string | null>(null)
  const analyzeTask = useTaskPoller(analyzeTaskId)
  const analyzeBusy = !!analyzeTask && (analyzeTask.status === 'pending' || analyzeTask.status === 'running')

  const analyzeMut = useMutation({
    mutationFn: () => api.post<{ task_id: string }>(`/novels/${novelId}/analyze`, {}),
    onSuccess: (r) => setAnalyzeTaskId(r.task_id),
    onError: (e: Error) => toast.error(e.message),
  })

  // 分析终态：成功 → 刷新剧本（弹窗自动解锁追加）；失败/取消 → 解除占用、可重试
  useEffect(() => {
    if (!analyzeTask) return
    if (analyzeTask.status === 'succeeded') {
      setAnalyzeTaskId(null)
      qc.invalidateQueries({ queryKey: ['novel', novelId] })
      toast.success('剧本分析完成，可继续追加章节')
    } else if (analyzeTask.status === 'failed' || analyzeTask.status === 'cancelled') {
      setAnalyzeTaskId(null)
      if (analyzeTask.status === 'failed')
        toast.error('剧本分析失败：' + (analyzeTask.error || '详情见任务中心'))
    }
  }, [analyzeTask?.status, novelId, qc, toast])
  // 章节列表刷新后（如上传新章节）同步默认追加范围
  useEffect(() => {
    setStart(upto + 1)
    setEnd(Math.min(upto + DEFAULT_BATCH, Math.max(upto + 1, total)))
  }, [upto, total])

  const mut = useMutation({
    mutationFn: () =>
      api.post<{ task_id: string }>(`/novels/${novelId}/adapt-continuation`, {
        project_id: projectId,
        chapter_start: Number(start),
        chapter_end: Number(end),
      }),
    onSuccess: (r) => setTaskId(r.task_id),
    onError: (e: Error) => toast.error(e.message),
  })

  // 追加任务终态：成功 → 刷新幕/分镜/项目/章节列表并关闭；失败/取消 → 解除 busy
  useEffect(() => {
    if (!task) return
    if (task.status === 'succeeded') {
      if (savedRef.current) return
      savedRef.current = true
      qc.invalidateQueries({ queryKey: ['episodes', projectId] })
      qc.invalidateQueries({ queryKey: ['segments', projectId] })
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['novel-chapters', novelId, projectId] })
      toast.success('章节已追加，刷新中…')
      onSaved()
    } else if (task.status === 'failed' || task.status === 'cancelled') {
      setTaskId(null)
    }
  }, [task?.status, projectId, novelId, qc, toast, onSaved])

  // 导入后续章节：合并进剧本 raw_text，供继续追加生成
  const appendMut = useMutation({
    mutationFn: () => api.post(`/novels/${novelId}/chapters/append`, { text: appendText }),
    onSuccess: () => {
      toast.success('新章节已合并，可继续追加改编')
      setAppendText('')
      qc.invalidateQueries({ queryKey: ['novel', novelId] })
      qc.invalidateQueries({ queryKey: ['novel-chapters', novelId, projectId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  async function handleAppendFile(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    const parts = await Promise.all(files.map((f) => f.text()))
    const joined = parts.join('\n\n')
    setAppendText((prev) => (prev ? `${prev}\n\n${joined}` : joined))
  }

  const canSubmit =
    !busy &&
    !mut.isPending &&
    novel?.analysis_status === 'done' &&
    Number.isFinite(Number(start)) &&
    Number.isFinite(Number(end)) &&
    Number(start) >= 1 &&
    Number(start) <= Number(end) &&
    Number(end) <= total

  return (
    <Modal
      open
      onClose={onClose}
      title="追加章节"
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            关闭
          </Button>
          <Button onClick={() => mut.mutate()} loading={mut.isPending || busy} disabled={!canSubmit}>
            {busy ? '改编中…' : '开始追加'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {novel && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Icon name="book" size={14} className="text-brand-600" />
            来源剧本：
            <span className="text-slate-800 font-medium">{novel.title}</span>
            <Badge variant="gray" size="sm">{total} 章</Badge>
          </div>
        )}

        {novel && novel.analysis_status !== 'done' && (
          <div className="rounded-lg border border-amber-200 bg-amber-500/10 px-3 py-2.5 space-y-2">
            <div className="flex items-start gap-2 text-xs text-amber-700">
              <Icon name="info" size={14} className="mt-0.5 shrink-0" />
              <span>
                追加章节需要先分析整本剧本（提取各章摘要与剧情要素，供续接保持人物/剧情一致）。
                当前剧本
                {novel.analysis_status === 'analyzing'
                  ? '正在分析中'
                  : novel.analysis_status === 'failed'
                    ? '分析失败'
                    : '尚未分析'}
                ，无法追加，请先完成分析。
              </span>
            </div>
            {analyzeBusy ? (
              <ProgressBar
                value={analyzeTask?.progress ?? 0}
                label={`剧本分析中… ${analyzeTask?.progress ?? 0}%`}
                variant="brand"
              />
            ) : (
              <Button size="sm" variant="outline" onClick={() => analyzeMut.mutate()} loading={analyzeMut.isPending}>
                <Icon name="play" size={12} className="mr-1" />
                立即分析剧本
              </Button>
            )}
          </div>
        )}

        {/* 上传后续章节（连载更新） */}
        <div className="border border-dashed border-brand-300/50 rounded-lg p-3 space-y-2 bg-brand-500/[0.03]">
          <div className="flex items-center gap-2 text-sm">
            <Icon name="upload" size={14} className="text-brand-600" />
            <span className="font-medium text-slate-700">上传后续章节</span>
            <span className="text-xs text-slate-400">剧本更新后，合并新章节即可继续追加生成</span>
          </div>
          <textarea
            value={appendText}
            onChange={(e) => setAppendText(e.target.value)}
            placeholder="粘贴后续章节文本，需包含章节标记（第X章 / Chapter N）"
            rows={4}
            className="input-base resize-none font-mono text-xs"
            disabled={appendMut.isPending}
          />
          <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-2 px-3 py-1.5 text-xs border border-slate-200 rounded-lg cursor-pointer hover:border-brand-300 transition-colors bg-white">
              <Icon name="upload" size={14} className="text-brand-600" />
              选择 .txt 文件（可多选）
              <input
                type="file"
                accept=".txt,.md,text/plain"
                multiple
                onChange={handleAppendFile}
                className="hidden"
              />
            </label>
            <Button
              size="sm"
              variant="outline"
              onClick={() => appendMut.mutate()}
              loading={appendMut.isPending}
              disabled={!appendText.trim() || !!busy}
            >
              合并到剧本
            </Button>
          </div>
          <p className="text-xs text-slate-400 flex items-start gap-1">
            <Icon name="info" size={12} className="mt-0.5 shrink-0" />
            合并后新章节从第 {total + 1} 章起编号，原有章节与追加断点不变。
          </p>
        </div>

        {/* 追加范围 */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-slate-400 mb-1.5 block">
              起始章（已追加到第 {upto} 章）
            </label>
            <input
              type="number"
              min={1}
              max={total}
              value={start}
              onChange={(e) => setStart(Number(e.target.value))}
              className="input-base"
              disabled={busy}
            />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1.5 block">结束章（≤ {total}）</label>
            <input
              type="number"
              min={1}
              max={total}
              value={end}
              onChange={(e) => setEnd(Number(e.target.value))}
              className="input-base"
              disabled={busy}
            />
          </div>
        </div>
        <p className="text-xs text-slate-400 flex items-start gap-1">
          <Icon name="info" size={12} className="mt-0.5 shrink-0" />
          章节必须按序续接：只能从断点下一章开始追加。建议每批 ≤3 章，避免改编质量下降。
        </p>

        {/* 章节列表 */}
        <div className="border border-slate-200 rounded-lg divide-y divide-slate-100 max-h-64 overflow-y-auto">
          {chapters.length === 0 && (
            <div className="p-4 text-sm text-slate-400">该剧本没有可识别的章节标记。</div>
          )}
          {chapters.map((c) => {
            const inRange = c.index >= Number(start) && c.index <= Number(end)
            return (
              <div
                key={c.index}
                className={cn(
                  'flex items-center gap-3 px-3 py-2 text-sm',
                  c.processed ? 'opacity-60' : '',
                  inRange && !c.processed ? 'bg-brand-500/5' : '',
                )}
              >
                <span className="text-xs font-mono text-slate-400 w-8 shrink-0">
                  {c.index}
                </span>
                <span className="text-slate-700 truncate flex-1 min-w-0">
                  {c.title || `第${c.index}章`}
                </span>
                {c.processed ? (
                  <Badge variant="green" size="sm">
                    <Icon name="check" size={11} />
                    已追加
                  </Badge>
                ) : inRange ? (
                  <Badge variant="blue" size="sm">本批</Badge>
                ) : (
                  <Badge variant="gray" size="sm">待追加</Badge>
                )}
              </div>
            )
          })}
        </div>

        {/* 任务进度 */}
        {busy && (
          <ProgressBar
            value={task!.progress}
            label={task!.status === 'running' ? `追加改编中 ${task!.progress}%` : '排队中…'}
            variant="brand"
          />
        )}
        {task?.status === 'failed' && (
          <div className="flex items-start gap-2 text-xs text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg px-3 py-2">
            <Icon name="alert-circle" size={12} className="mt-0.5 shrink-0" />
            <span>追加失败：{task.error}</span>
          </div>
        )}
      </div>
    </Modal>
  )
}