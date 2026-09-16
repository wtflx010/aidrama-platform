/**
 * 剧本库管理页：导入/智能体生成剧本 → 直接生成项目。
 *
 * 流程：
 * 1. 导入剧本（粘贴或上传 .txt/.md 文件），或由智能体写入剧本库
 * 2. 点击剧本卡片进入剧本详情页（总览 + 分镜时间轴 + 资产绑定 + AI 润色）
 * 3. 在详情页配置生成参数（分镜时长/视觉风格）→ 生成项目
 *（剧本是结构化产物，跳过 LLM「分析」步骤，直接改编为项目）
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Novel } from '../api/types'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { Modal } from '../components/ui/Modal'
import { EmptyState } from '../components/ui/EmptyState'
import { useToast } from '../components/ui/Toast'
import { useConfirm } from '../components/ui/ConfirmDialog'
import { Icon } from '../lib/icons'

const STATUS_MAP: Record<
  string,
  { text: string; variant: 'gray' | 'blue' | 'green' | 'red' }
> = {
  pending: { text: '新剧本', variant: 'gray' },
  analyzing: { text: '分析中', variant: 'blue' },
  done: { text: '已就绪', variant: 'green' },
  failed: { text: '失败', variant: 'red' },
}

export default function Novels() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const confirm = useConfirm()
  const [showUpload, setShowUpload] = useState(false)

  const { data: novels = [] } = useQuery({
    queryKey: ['novels'],
    queryFn: () => api.get<Novel[]>('/novels'),
  })

  const del = useMutation({
    mutationFn: (id: string) => api.del(`/novels/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['novels'] })
      toast.success('剧本已删除')
    },
  })

  // 2026-08-27：重新生成剧本海报（贴合剧本 + 中文准确），异步任务走任务中心
  const regenPoster = useMutation({
    mutationFn: (id: string) => api.post('/novels/' + id + '/poster/regenerate'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['novels'] })
      toast.success('海报重新生成中，完成后自动更新')
    },
    onError: (e: Error) => toast.error('海报重生成失败：' + e.message),
  })

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    if (
      await confirm({
        title: '删除剧本',
        message: '确认删除该剧本？关联项目不会被删除。',
        danger: true,
      })
    ) {
      del.mutate(id)
    }
  }

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8 animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
            <span className="w-9 h-9 rounded-xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center">
              <Icon name="book" size={20} className="text-brand-600" />
            </span>
            剧本库
          </h1>
          <p className="text-sm text-slate-400 mt-1.5 ml-12">
            导入/生成剧本 → 详情页查看分镜时间轴并生成项目
          </p>
        </div>
        <Button
          leftIcon={<Icon name="upload" size={16} />}
          onClick={() => setShowUpload(true)}
        >
          导入剧本
        </Button>
      </div>

      {novels.length === 0 ? (
        <EmptyState
          icon="book"
          title="还没有剧本"
          description="导入一个剧本（或让智能体写好剧本），即可在详情页查看分镜并生成项目"
          action={
            <Button
              leftIcon={<Icon name="upload" size={16} />}
              onClick={() => setShowUpload(true)}
            >
              导入剧本
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {novels.map((n) => {
            const st = STATUS_MAP[n.analysis_status] ?? {
              text: n.analysis_status,
              variant: 'gray' as const,
            }
            return (
              <Card
                key={n.id}
                hover
                className="p-3 group cursor-pointer flex items-start gap-3"
                onClick={() => navigate('/novels/' + n.id)}
              >
                {/* 剧本海报缩略图（写剧本时自动生成；无海报时占位） */}
                <div className="shrink-0 w-16 h-24 rounded-lg overflow-hidden bg-gradient-to-br from-slate-100 to-slate-200 relative">
                  {n.poster_url ? (
                    <div className="relative w-full h-full group/poster">
                      <img
                        src={n.poster_url}
                        alt={n.title}
                        className="w-full h-full object-contain transition-transform duration-300 group-hover:scale-[1.05]"
                      />
                      <button
                        onClick={(e) => { e.stopPropagation(); regenPoster.mutate(n.id) }}
                        title="重新生成海报（贴合剧本 + 中文准确）"
                        className="absolute inset-0 flex items-center justify-center bg-slate-900/50 text-white opacity-0 group-hover/poster:opacity-100 transition-opacity text-xs gap-1"
                      >
                        <Icon name="refresh" size={14} /> 重生成
                      </button>
                    </div>
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      <Icon name="book" size={22} className="text-slate-300" />
                    </div>
                  )}
                </div>
                <div className="min-w-0 flex-1 flex flex-col gap-1.5">
                  <div className="flex items-center gap-2 min-w-0">
                    <h3 className="font-semibold text-slate-900 truncate">{n.title}</h3>
                    <Badge variant={st.variant} dot className="shrink-0">{st.text}</Badge>
                  </div>
                  <div className="text-xs text-slate-400 flex items-center gap-3">
                    <span className="flex items-center gap-1"><Icon name="layers" size={12} />{n.chapters_count} 章</span>
                    <span className="flex items-center gap-1"><Icon name="type" size={12} />{n.word_count.toLocaleString()} 字</span>
                  </div>
                  {n.project_id && (
                    <Badge variant="green" className="w-fit">
                      <Icon name="check" size={12} /> 已生成项目
                    </Badge>
                  )}
                  {n.error && (
                    <p className="text-xs text-rose-600 line-clamp-2 flex items-start gap-1">
                      <Icon name="alert-circle" size={12} className="mt-0.5 shrink-0" />
                      {n.error}
                    </p>
                  )}
                  <div className="flex items-center justify-between mt-auto pt-1 text-xs text-slate-400">
                    <span className="flex items-center gap-1">
                      <Icon name="clock" size={12} />
                      {new Date(n.created_at).toLocaleString('zh-CN')}
                    </span>
                    <button
                      onClick={(e) => handleDelete(e, n.id)}
                      className="text-slate-400 hover:text-rose-500 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                      aria-label="删除剧本"
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </div>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      {showUpload && (
        <UploadDialog
          onClose={() => setShowUpload(false)}
          onUploaded={(id) => {
            setShowUpload(false)
            qc.invalidateQueries({ queryKey: ['novels'] })
            navigate(`/novels/${id}`)
          }}
        />
      )}
    </div>
  )
}

function UploadDialog({
  onClose,
  onUploaded,
}: {
  onClose: () => void
  onUploaded: (id: string) => void
}) {
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    const parts = await Promise.all(files.map((f) => f.text()))
    setText((prev) => (prev ? `${prev}\n\n${parts.join('\n\n')}` : parts.join('\n\n')))
    if (!title) setTitle(files[0].name.replace(/\.txt$/i, ''))
  }

  async function submit() {
    if (!title.trim()) {
      setError('请输入标题')
      return
    }
    if (!text.trim()) {
      setError('请粘贴或导入剧本内容')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const n = await api.post<Novel>('/novels/upload', { title: title.trim(), text })
      toast.success('剧本导入成功')
      onUploaded(n.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="导入剧本"
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} loading={loading}>
            导入
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">标题</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="剧本标题"
            className="input-base"
          />
        </div>
        <div>
          <label className="text-xs text-slate-400 mb-1.5 block">
            剧本内容（粘贴或上传 .txt/.md）
          </label>
          <label className="flex items-center gap-2 px-3 py-2.5 surface cursor-pointer hover:border-brand-500/30 transition-colors mb-2">
            <Icon name="upload" size={16} className="text-brand-600" />
            <span className="text-sm text-slate-600">
              {text ? '重新选择文件' : '选择 .txt / .md 文件（可多选）'}
            </span>
            <input
              type="file"
              accept=".txt,.md,text/plain"
              multiple
              onChange={handleFile}
              className="hidden"
            />
          </label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="粘贴剧本全文（分集请用「第X集」标记行分隔）"
            rows={10}
            className="input-base resize-none font-mono text-xs"
          />
          <p className="text-xs text-slate-400 mt-1.5 flex items-center gap-1">
            <Icon name="type" size={12} />
            {text.length.toLocaleString()} 字
          </p>
        </div>

        {error && (
          <div className="flex items-start gap-2 text-sm text-rose-600 bg-rose-500/10 border border-rose-200 rounded-lg px-3 py-2">
            <Icon name="alert-circle" size={14} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>
    </Modal>
  )
}
