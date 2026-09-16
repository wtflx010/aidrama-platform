/**
 * 参考音频上传字段：支持文件上传（base64）+ URL 手动输入 + 试听 + 清除。
 * 用于角色声线档案和旁白声线配置的 reference_audio_url 编辑。
 */
import { useRef, useState } from 'react'
import { uploadAudio } from '../../api/client'
import { Icon } from '../../lib/icons'

export function AudioUploadField({
  url,
  onUrlChange,
  referenceText,
  onReferenceTextChange,
}: {
  url: string
  onUrlChange: (url: string) => void
  referenceText: string
  onReferenceTextChange: (text: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setError(null)
    setUploading(true)
    try {
      const uploadedUrl = await uploadAudio(file)
      onUrlChange(uploadedUrl)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="space-y-2">
      <div>
        <label className="text-xs text-slate-500 mb-1 block">参考音频（CosyVoice zero_shot 克隆）</label>
        <div className="flex gap-2">
          <input value={url} onChange={(e) => onUrlChange(e.target.value)}
            className="input-base flex-1" placeholder="上传文件或粘贴 URL" />
          <input ref={fileRef} type="file" accept=".wav,.mp3,.flac,.ogg,.m4a"
            onChange={handleFile} className="hidden" />
          <button type="button"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="shrink-0 px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-300 bg-white hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1">
            <Icon name="upload" size={12} />
            {uploading ? '上传中…' : '上传'}
          </button>
          {url && (
            <button type="button" onClick={() => onUrlChange('')}
              className="shrink-0 px-2 py-1.5 text-xs text-rose-500 hover:text-rose-600 border border-slate-300 rounded-lg">
              清除
            </button>
          )}
        </div>
        {error && <p className="text-xs text-rose-600 mt-1">{error}</p>}
        <p className="text-[10px] text-slate-400 mt-1">
          支持 wav/mp3/flac，建议 3-10 秒清晰人声样本
        </p>
      </div>
      {url && (
        <audio src={url} controls className="w-full h-8" />
      )}
      <div>
        <label className="text-xs text-slate-500 mb-1 block">参考音频对应文本</label>
        <input value={referenceText} onChange={(e) => onReferenceTextChange(e.target.value)}
          className="input-base w-full" placeholder="参考音频中说的话（提升克隆效果）" />
      </div>
    </div>
  )
}
