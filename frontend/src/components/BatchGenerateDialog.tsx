import { useRef, useState } from 'react'
import { Modal } from './ui/Modal'
import { Button } from './ui/Button'
import { Icon } from '../lib/icons'
import { uploadImage } from '../api/client'

export interface BatchGenState {
  reference_src: 'none' | 'custom' | 'prev_tail'
  custom_first_frame_url?: string
  overwrite: boolean
}

interface Props {
  open: boolean
  onClose: () => void
  busy: boolean
  onConfirm: (state: BatchGenState) => void
}

const SRC_OPTIONS = [
  { value: 'none' as const, label: '无', desc: '跟随分镜现有逻辑：有资产图 → 图生视频；无资产 → 文生视频' },
  { value: 'custom' as const, label: '手工上传', desc: '所有分镜共用你上传的这张图作首帧（图生视频）' },
  { value: 'prev_tail' as const, label: '上一分镜尾帧', desc: '第 1 镜无上一镜自动跳过；第 2~N 镜首帧接上一镜尾帧，按分镜顺序串行生成' },
]

export function BatchGenerateDialog({ open, onClose, busy, onConfirm }: Props) {
  const [referenceSrc, setReferenceSrc] = useState<'none' | 'custom' | 'prev_tail'>('none')
  const [customUrl, setCustomUrl] = useState('')
  const [uploading, setUploading] = useState(false)
  const [overwrite, setOverwrite] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const handleFile = async (f: File | undefined) => {
    if (!f) return
    setUploading(true)
    try {
      const url = await uploadImage(f)
      setCustomUrl(url)
    } catch {
      setCustomUrl('')
    } finally {
      setUploading(false)
    }
  }

  const confirm = () => {
    onConfirm({
      reference_src: referenceSrc,
      ...(referenceSrc === 'custom' && customUrl ? { custom_first_frame_url: customUrl } : {}),
      overwrite,
    })
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="生成所有分镜"
      size="md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button size="sm" variant="primary" onClick={confirm} loading={busy}>开始生成</Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* 初始帧来源 */}
        <div>
          <div className="text-sm font-medium text-slate-700 mb-2">初始帧来源</div>
          <div className="space-y-2">
            {SRC_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={`flex items-start gap-2.5 rounded-xl border p-3 cursor-pointer transition-colors ${
                  referenceSrc === opt.value ? 'border-brand-400 bg-brand-50' : 'border-slate-200 hover:border-brand-300'
                }`}
              >
                <input type="radio" className="mt-0.5 accent-brand-500" name="ref-src" checked={referenceSrc === opt.value} onChange={() => setReferenceSrc(opt.value)} />
                <span>
                  <span className="block text-sm font-medium text-slate-800">{opt.label}</span>
                  <span className="block text-xs text-slate-500 mt-0.5">{opt.desc}</span>
                </span>
              </label>
            ))}
          </div>
          {referenceSrc === 'custom' && (
            <div className="mt-2 px-1 flex items-center gap-2">
              <input
                type="file"
                accept="image/*"
                className="hidden"
                ref={fileRef}
                onChange={(e) => {
                  void handleFile(e.target.files?.[0])
                  e.target.value = ''
                }}
              />
              <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()} loading={uploading} leftIcon={<Icon name="upload" size={13} />}>
                {customUrl ? '已选首帧图 ✓' : '上传首帧图'}
              </Button>
              {customUrl && <span className="text-[10px] text-slate-400 truncate max-w-[200px]">{customUrl.split('/').pop()}</span>}
            </div>
          )}
        </div>
        {/* 覆盖已生成视频 */}
        <label className="flex items-start gap-2.5 rounded-xl border border-slate-200 p-3 cursor-pointer">
          <input type="checkbox" className="mt-0.5 accent-brand-500" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
          <span>
            <span className="block text-sm font-medium text-slate-800">覆盖已生成视频</span>
            <span className="block text-xs text-slate-500 mt-0.5">勾选：重新生成已有成片的分镜；不勾选：已有成片的分镜直接跳过，只为没有成片的分镜生成</span>
          </span>
        </label>
      </div>
    </Modal>
  )
}
