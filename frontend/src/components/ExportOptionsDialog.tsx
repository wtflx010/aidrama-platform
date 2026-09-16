import { useState } from 'react'
import { Modal } from './ui/Modal'
import { Button } from './ui/Button'

export interface ExportOptions {
  include_voice: boolean
  include_subtitle: boolean
  burn_subtitle: boolean
  include_bgm: boolean
  include_sfx: boolean
}

interface Props {
  open: boolean
  onClose: () => void
  busy: boolean
  onConfirm: (o: ExportOptions) => void
}

export function ExportOptionsDialog({ open, onClose, busy, onConfirm }: Props) {
  const [includeSubtitle, setIncludeSubtitle] = useState(true)
  const [includeVoice, setIncludeVoice] = useState(false)
  const [includeBgm, setIncludeBgm] = useState(true)
  const [includeSfx, setIncludeSfx] = useState(true)

  const confirm = () => {
    onConfirm({
      include_voice: includeVoice,
      include_subtitle: includeSubtitle,
      burn_subtitle: includeSubtitle,
      include_bgm: includeBgm,
      include_sfx: includeSfx,
    })
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="合成完整视频"
      size="sm"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button size="sm" variant="primary" onClick={confirm} loading={busy}>开始合成</Button>
        </>
      }
    >
      <div className="space-y-2.5">
        <label className="flex items-center gap-2.5 rounded-xl border border-slate-200 p-3 cursor-pointer">
          <input type="checkbox" className="accent-brand-500" checked={includeSubtitle} onChange={(e) => setIncludeSubtitle(e.target.checked)} />
          <span className="text-sm text-slate-700">
            <span className="block font-medium">字幕</span>
            <span className="block text-xs text-slate-400">台词/旁白（已去除「人名：」「旁白：」字样），不勾则不出字幕</span>
          </span>
        </label>
        <label className="flex items-center gap-2.5 rounded-xl border border-slate-200 p-3 cursor-pointer">
          <input type="checkbox" className="accent-brand-500" checked={includeVoice} onChange={(e) => setIncludeVoice(e.target.checked)} />
          <span className="text-sm text-slate-700">
            <span className="block font-medium">配音</span>
            <span className="block text-xs text-slate-400">TTS 口播音轨（覆盖画面自带声音）；默认关，直接用视频原生音轨</span>
          </span>
        </label>
        <label className="flex items-center gap-2.5 rounded-xl border border-slate-200 p-3 cursor-pointer">
          <input type="checkbox" className="accent-brand-500" checked={includeBgm} onChange={(e) => setIncludeBgm(e.target.checked)} />
          <span className="text-sm text-slate-700">
            <span className="block font-medium">背景音乐</span>
            <span className="block text-xs text-slate-400">本幕已选的 BGM</span>
          </span>
        </label>
        <label className="flex items-center gap-2.5 rounded-xl border border-slate-200 p-3 cursor-pointer">
          <input type="checkbox" className="accent-brand-500" checked={includeSfx} onChange={(e) => setIncludeSfx(e.target.checked)} />
          <span className="text-sm text-slate-700">
            <span className="block font-medium">音效</span>
            <span className="block text-xs text-slate-400">本镜已选的 SFX</span>
          </span>
        </label>
        <p className="text-xs text-slate-400 px-1">片段之间不做转场动画（纯硬切），方便后续自行剪辑。</p>
      </div>
    </Modal>
  )
}
