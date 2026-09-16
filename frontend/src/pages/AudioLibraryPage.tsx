import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, uploadAudio } from '../api/client'
import { Badge } from '../components/ui/Badge'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import { useToast } from '../components/ui/Toast'
import { Icon } from '../lib/icons'
import { cn } from '../lib/cn'

export interface BgmItem { id: string; project_id: string | null; episode_id: string | null; emotion: string; audio_url: string | null; duration: number; volume: number; status: string; source: string; prompt: string; created_at: string | null }
export interface SfxItem { id: string; project_id: string | null; segment_id: string | null; sfx_type: string; sfx_name: string; audio_url: string | null; duration: number; volume: number; status: string; source: string; created_at: string | null }
export interface VoicePreset { voice_id: string; gender: string; age_group: string; sample_text: string }

type Tab = 'bgm' | 'sfx' | 'narrator'

const EMOTIONS = ['平静', '温馨', '紧张', '悲伤', '愤怒', '恐惧', '欢快', '史诗']
const EMOTION_CLS: Record<string, string> = {
  温馨: 'bg-rose-500/10 text-rose-600 border-rose-200',
  紧张: 'bg-red-500/10 text-red-600 border-red-200',
  悲伤: 'bg-blue-500/10 text-blue-600 border-blue-200',
  欢快: 'bg-amber-500/10 text-amber-600 border-amber-200',
  愤怒: 'bg-orange-600/10 text-orange-600 border-orange-200',
  恐惧: 'bg-purple-500/10 text-purple-600 border-purple-200',
  史诗: 'bg-indigo-500/10 text-indigo-600 border-indigo-200',
  平静: 'bg-slate-500/10 text-slate-600 border-slate-200',
}

export default function AudioLibraryPage() {
  const [tab, setTab] = useState<Tab>('bgm')
  return (
    <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 animate-fade-in">
      <div className="flex items-center gap-2.5 mb-6">
        <span className="w-9 h-9 rounded-xl bg-gradient-brand-subtle border border-brand-200 flex items-center justify-center">
          <Icon name="music" size={20} className="text-brand-600" />
        </span>
        <div>
          <h1 className="text-2xl font-bold text-slate-900">音频库</h1>
          <p className="text-sm text-slate-400 mt-1">背景音乐 · 音效 · 旁白声线（全局资源，项目删除仅解绑保留）</p>
        </div>
      </div>
      <div className="flex items-center gap-1 mb-6 p-1 bg-slate-100/80 rounded-xl w-fit">
        {([['bgm', '背景音乐', 'music'], ['sfx', '音效', 'headphones'], ['narrator', '旁白声线', 'captions']] as [Tab, string, string][]).map(([key, label, icon]) => (
          <button key={key} onClick={() => setTab(key)}
            className={cn('inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-colors', tab === key ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700')}>
            <Icon name={icon} size={15} />{label}
          </button>
        ))}
      </div>
      {tab === 'bgm' && <BgmLibrarySection />}
      {tab === 'sfx' && <SfxLibrarySection />}
      {tab === 'narrator' && <NarratorLibrarySection />}
    </div>
  )
}

function BgmLibrarySection() {
  const qc = useQueryClient()
  const toast = useToast()
  const [showGen, setShowGen] = useState(false)
  const [genEmotion, setGenEmotion] = useState('平静')
  const [genPrompt, setGenPrompt] = useState('')
  const [genDuration, setGenDuration] = useState(20)
  const [genBusy, setGenBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['audio-library'],
    queryFn: () => api.get<{ bgm: BgmItem[]; sfx: SfxItem[] }>('/audio/library'),
  })
  const bgms = data?.bgm ?? []

  async function generate() {
    setGenBusy(true); setError(null)
    try {
      await api.post('/audio/library/bgm/generate', { emotion: genEmotion, prompt: genPrompt.trim() || undefined, duration: genDuration })
      setShowGen(false); setGenPrompt('')
      qc.invalidateQueries({ queryKey: ['audio-library'] })
      toast.success('BGM 生成完成，已加入音频库')
    } catch (e) { setError((e as Error).message) } finally { setGenBusy(false) }
  }

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return
    setError(null)
    try {
      for (const f of Array.from(files).slice(0, 5)) {
        const url = await uploadAudio(f)
        await api.post('/audio/library/bgm/upload', { audio_url: url, emotion: genEmotion, prompt: '用户上传 ' + f.name })
      }
      qc.invalidateQueries({ queryKey: ['audio-library'] })
      toast.success('BGM 上传成功')
    } catch (e) { setError((e as Error).message) }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold text-slate-900">背景音乐</h2>
        <div className="flex items-center gap-2">
          <label className="cursor-pointer">
            <span className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-2 rounded-lg border border-slate-200 bg-white text-slate-600 hover:border-brand-400 transition-colors">
              <Icon name="upload" size={15} />上传 BGM
            </span>
            <input type="file" accept="audio/*" multiple className="hidden" onChange={(e) => { void upload(e.target.files); e.target.value = '' }} />
          </label>
          <Button leftIcon={<Icon name="sparkles" size={15} />} onClick={() => setShowGen(!showGen)}>{showGen ? '收起' : '提示词生成'}</Button>
        </div>
      </div>
      {showGen && (
        <div className="rounded-xl border border-brand-200 bg-brand-500/[0.04] p-4 mb-5 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">情感氛围</label>
              <div className="flex flex-wrap gap-1.5">
                {EMOTIONS.map((e) => (
                  <button key={e} onClick={() => setGenEmotion(e)}
                    className={cn('px-2.5 py-1 rounded-full border text-xs transition-colors', genEmotion === e ? 'bg-brand-500 text-white border-brand-500' : 'bg-white text-slate-500 border-slate-200')}>
                    {e}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">时长（秒）</label>
              <select value={genDuration} onChange={(e) => setGenDuration(Number(e.target.value))} className="input-base">
                {[10, 15, 20, 30, 60].map((d) => <option key={d} value={d}>{d}s</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">提示词（可选，留空按情感自动生成）</label>
              <input value={genPrompt} onChange={(e) => setGenPrompt(e.target.value)} placeholder="如：温馨的钢琴曲，慢板，约 90 拍" className="input-base" />
            </div>
          </div>
          {error && <p className="text-xs text-rose-600"><Icon name="alert-circle" size={12} className="inline mr-1" />{error}</p>}
          <div className="flex justify-end"><Button onClick={generate} loading={genBusy} leftIcon={<Icon name="sparkles" size={14} />}>一键生成</Button></div>
        </div>
      )}
      {isLoading ? (
        <div className="text-sm text-slate-400 py-10 text-center">加载中…</div>
      ) : bgms.length === 0 ? (
        <EmptyState icon="music" title="音频库还没有 BGM" description="点击右上角「上传 BGM」或「提示词生成」添加" />
      ) : (
        <div className="space-y-2">
          {bgms.map((b) => (
            <div key={b.id} className="rounded-xl border border-slate-200 bg-white px-4 py-3 flex items-center gap-4">
              {b.audio_url ? <audio controls preload="none" className="h-10 w-56 shrink-0" src={b.audio_url} /> : (
                <div className="w-56 h-10 rounded bg-slate-50 border border-slate-100 flex items-center justify-center text-[10px] text-slate-300 shrink-0">音频未就绪</div>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-medium border', EMOTION_CLS[b.emotion] || 'bg-slate-500/10 text-slate-500')}>{b.emotion}</span>
                  <Badge variant="gray" size="sm">{b.source}</Badge>
                  {b.project_id === null && <Badge variant="purple" size="sm">全局</Badge>}
                </div>
                <p className="text-sm text-slate-600 mt-1 truncate">{b.prompt.slice(0, 100)}</p>
              </div>
              <div className="text-xs text-slate-400 shrink-0">{Math.round(b.duration)}s</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SfxLibrarySection() {
  const qc = useQueryClient()
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['audio-library'],
    queryFn: () => api.get<{ bgm: BgmItem[]; sfx: SfxItem[] }>('/audio/library'),
  })
  const sfxs = data?.sfx ?? []

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return
    setError(null)
    try {
      for (const f of Array.from(files).slice(0, 10)) {
        const url = await uploadAudio(f)
        const name = f.name.replace(/\.[^.]+$/, '')
        await api.post('/audio/library/sfx/upload', { sfx_type: 'upload', sfx_name: name, audio_url: url })
      }
      qc.invalidateQueries({ queryKey: ['audio-library'] })
      toast.success('音效上传成功')
    } catch (e) { setError((e as Error).message) }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold text-slate-900">音效</h2>
        <label className="cursor-pointer">
          <span className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-2 rounded-lg border border-slate-200 bg-white text-slate-600 hover:border-brand-400 transition-colors">
            <Icon name="upload" size={15} />上传音效
          </span>
          <input type="file" accept="audio/*" multiple className="hidden" onChange={(e) => { void upload(e.target.files); e.target.value = '' }} />
        </label>
      </div>
      {error && <p className="text-xs text-rose-600 mb-2"><Icon name="alert-circle" size={12} className="inline mr-1" />{error}</p>}
      {isLoading ? (
        <div className="text-sm text-slate-400 py-10 text-center">加载中…</div>
      ) : sfxs.length === 0 ? (
        <EmptyState icon="headphones" title="音频库还没有音效" description="点击右上角「上传音效」添加" />
      ) : (
        <div className="space-y-2">
          {sfxs.map((s) => (
            <div key={s.id} className="rounded-xl border border-slate-200 bg-white px-4 py-3 flex items-center gap-4">
              {s.audio_url ? <audio controls preload="none" className="h-9 w-52 shrink-0" src={s.audio_url} /> : (
                <div className="w-52 h-9 rounded bg-slate-50 border border-slate-100 flex items-center justify-center text-[10px] text-slate-300 shrink-0">音频未就绪</div>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium border bg-slate-500/10 text-slate-600">{s.sfx_type}</span>
                  <span className="font-medium text-slate-800">{s.sfx_name}</span>
                  <Badge variant="gray" size="sm">{s.source}</Badge>
                  {s.project_id === null && <Badge variant="purple" size="sm">全局</Badge>}
                </div>
              </div>
              <div className="text-xs text-slate-400 shrink-0">{Math.round(s.duration)}s</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function NarratorLibrarySection() {
  const { data: presets = [] } = useQuery({
    queryKey: ['voice-presets'],
    queryFn: () => api.get<VoicePreset[]>('/voice-presets'),
  })
  const GENDER: Record<string, string> = { male: '男声', female: '女声', neutral: '中性' }
  const AGE: Record<string, string> = { child: '儿童', youth: '青年', middle: '中年', elder: '老年' }
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2"><Icon name="captions" size={18} className="text-brand-600" />旁白声线</h2>
        <span className="text-xs text-slate-400">系统预置声线，可在项目中引用配音</span>
      </div>
      {presets.length === 0 ? (
        <EmptyState icon="captions" title="暂无预置声线" description="CosyVoice 服务在线时自动注册预置声音" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {presets.map((p) => (
            <div key={p.voice_id} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex items-center gap-2 mb-2"><Icon name="captions" size={16} className="text-brand-600" /><span className="font-semibold text-slate-800">{p.voice_id}</span></div>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {p.gender && <Badge variant="blue">{GENDER[p.gender] || p.gender}</Badge>}
                {p.age_group && <Badge variant="gray">{AGE[p.age_group] || p.age_group}</Badge>}
              </div>
              {p.sample_text && <p className="text-xs text-slate-400 rounded bg-slate-50 border border-slate-100 px-2.5 py-2">「{p.sample_text}」</p>}
            </div>
          ))}
        </div>
      )}
      <p className="text-xs text-slate-400 mt-4 flex items-start gap-1.5"><Icon name="info" size={12} className="mt-0.5 shrink-0" />旁白声线在项目「音乐音效」页签的旁白声线中应用（支持上传参考音频定制）。</p>
    </div>
  )
}
