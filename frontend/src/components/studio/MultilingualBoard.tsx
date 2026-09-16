/**
 * 多语言出海版块（P2-5）：选幕 + 目标语言 → 触发「翻译+配音+SRT 导出」任务，
 * 轮询完成拿到 SRT 下载链接（result_url）。复用任务中心轮询链路。
 */
import { useEffect, useState } from 'react'
import { LANGUAGES, multilingualApi } from '../../api/rework'
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

export function MultilingualBoard({ projectId, episodes, open, onClose }: Props) {
  const toast = useToast()
  const [episodeId, setEpisodeId] = useState('')
  const [lang, setLang] = useState('eng')
  const [doTts, setDoTts] = useState(true)
  const [taskId, setTaskId] = useState<string | null>(null)

  useEffect(() => {
    if (open && !episodeId && episodes.length > 0) setEpisodeId(episodes[0].id)
  }, [open, episodeId, episodes])

  const task = useTaskPoller(taskId)
  const running = !!task && (task.status === 'pending' || task.status === 'running')
  const eid = episodeId || (episodes[0]?.id ?? '')

  useEffect(() => {
    if (!task || !taskId) return
    if (task.status === 'succeeded') {
      toast.success('多语言导出完成')
      setTaskId(null)
    } else if (task.status === 'failed') {
      toast.error('多语言导出失败：' + (task.error || ''))
      setTaskId(null)
    } else if (task.status === 'cancelled') setTaskId(null)
  }, [task, taskId, toast])

  function start() {
    if (!eid) return
    multilingualApi
      .trigger(projectId, eid, lang, doTts)
      .then((r) => setTaskId(r.task_id))
      .catch((e: Error) => toast.error(e.message))
  }

  return (
    <Modal open={open} onClose={onClose} title="多语言配音与字幕导出" size="lg" footer={
      <Button variant="ghost" size="sm" onClick={onClose}>关闭</Button>
    }>
      <div className="space-y-4">
        {/* 幕 + 语言 */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Icon name="film" size={15} className="text-brand-600" />
            <select
              value={episodeId}
              onChange={(e) => setEpisodeId(e.target.value)}
              className="h-8 flex-1 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40"
            >
              {episodes.map((ep) => (
                <option key={ep.id} value={ep.id}>幕 {ep.index + 1} · {ep.title || '未命名'}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <Icon name="map-pin" size={15} className="text-brand-600" />
            <select
              value={lang}
              onChange={(e) => setLang(e.target.value)}
              className="h-8 flex-1 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/40"
            >
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>{l.label}</option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-600 select-none">
            <input type="checkbox" checked={doTts} onChange={(e) => setDoTts(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500/40" />
            同时生成目标语言配音（生成翻译版对白音频）
          </label>
        </div>

        <Button variant="primary" size="sm" onClick={start} loading={running} disabled={!eid}>
          {running ? '导出中…' : '翻译 + 导出字幕/配音'}
        </Button>

        {/* 结果：SRT 下载链接 */}
        {task?.status === 'succeeded' && task.result_url && (
          <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3">
            <div className="text-sm font-semibold text-emerald-700 mb-1">导出完成，字幕文件（SRT）已生成</div>
            <a
              href={task.result_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700 break-all"
            >
              <Icon name="file-text" size={14} /> {task.result_url}
            </a>
            <div className="mt-2 flex items-center gap-1 text-xs text-slate-500">
              <Badge variant="green" size="sm">SRT</Badge>
              {doTts ? '已生成目标语言配音' : '仅字幕（未生成配音）'}
            </div>
          </div>
        )}

        {running && (
          <div className="flex items-center gap-2 text-sm text-slate-500 py-1">
            <Spinner /> 正在翻译并导出，请稍候…
          </div>
        )}
        {!task && !running && (
          <p className="text-xs text-slate-400">将本幕对白/旁白/字幕翻译为目标语言，生成 SRT 字幕文件并（可选）配音。</p>
        )}
      </div>
    </Modal>
  )
}
