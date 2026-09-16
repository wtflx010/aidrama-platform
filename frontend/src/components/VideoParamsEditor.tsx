/**
 * 视频生成参数编辑器（项目级）：帧率 / 清晰度 / 视频大小 / 推理步数 / 引导强度 / Turbo / 种子。
 * 与分镜面板（StudioRightPanel）的选项集一致，供「项目设置」弹窗与「创建项目确认卡」复用。
 * 值结构与 Segment.gen_params 同构：fps/res/video_size/steps/cfg/seed/turbo（均字符串）。
 */
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'
import { aspectRatioOptions, genResolutionOptions } from '../lib/videoConfig'

export interface VideoParams {
  fps?: string | number | null
  res?: string | null
  video_size?: string | null
  steps?: string | number | null
  cfg?: string | number | null
  seed?: string | number | null
  turbo?: string | null
}

const FPS_OPTIONS = [
  { value: '16', label: '16fps' },
  { value: '24', label: '24fps' },
  { value: '30', label: '30fps' },
  { value: '60', label: '60fps' },
]
// 生成档位（走 lib/videoConfig 的 genResolutionOptions：0.1MP~1.0MP 统一级联档；1080p 是超分档，不进入生成下拉）
const RES_OPTIONS = genResolutionOptions()

const STEPS_OPTIONS = [
  { value: '4', label: '4步 Turbo' },
  { value: '6', label: '6步' },
  { value: '8', label: '8步' },
  { value: '16', label: '16步' },
  { value: '20', label: '20步' },
]
const CFG_OPTIONS = [
  { value: '1.0', label: '1.0（默认）' },
  { value: '2.0', label: '2.0' },
  { value: '3.0', label: '3.0' },
  { value: '4.0', label: '4.0' },
  { value: '5.0', label: '5.0' },
]
const TURBO_OPTIONS = [
  { value: 'high', label: 'Turbo 高' },
  { value: 'mid', label: 'Turbo 中' },
  { value: 'low', label: 'Turbo 低' },
]

/** 选项为空时（未设置）显示「跟随默认」 */
const EMPTY_LABEL = '跟随默认'

function picked(options: { value: string; label: string }[], cur: unknown): string {
  const s = String(cur ?? '')
  return options.some((o) => o.value === s) ? s : ''
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-slate-500 mb-1">{label}</label>
      {children}
    </div>
  )
}

export function VideoParamsEditor({ value, onChange, title }: {
  value: VideoParams | null | undefined
  onChange: (next: VideoParams) => void
  title?: string
}) {
  const v = value || {}
  const set = (k: keyof VideoParams, val: string) => onChange({ ...v, [k]: val })
  const sel = cn(
    'w-full bg-white border border-slate-200 rounded-lg px-2 py-1.5 text-sm text-slate-700',
    'focus:outline-none focus:ring-1 focus:ring-brand-500/40 focus:border-brand-400',
  )

  return (
    <div>
      <div className="text-xs font-medium text-slate-600 flex items-center gap-1 mb-1.5">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-slate-400"><rect x="2" y="4" width="20" height="16" rx="2" strokeWidth="2" /></svg>
        {title ?? '视频生成参数（项目级）'}
      </div>
      <p className="text-[11px] text-slate-400 mb-2">
        批量生成/项目统一出片时按下列参数执行；个别分镜在右面板单独设置的参数以分镜为准。
        帧率：H3 原生 24fps，成片导出统一归一为 24fps（非 24 档仅影响生成前进运动节奏，不改变成片帧率）。
      </p>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
        <Row label="帧率">
          <select className={sel} value={picked(FPS_OPTIONS, v.fps)} onChange={(e) => set('fps', e.target.value)}>
            <option value="">{EMPTY_LABEL}</option>
            {FPS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Row>
        <Row label="清晰度">
          <select className={sel} value={picked(RES_OPTIONS, v.res)} onChange={(e) => set('res', e.target.value)}>
            <option value="">{EMPTY_LABEL}</option>
            {RES_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Row>
        <Row label="视频大小">
          <select className={sel} value={picked(aspectRatioOptions(v.res), v.video_size)} onChange={(e) => set('video_size', e.target.value)}>
            <option value="">{EMPTY_LABEL}</option>
            {aspectRatioOptions(v.res).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Row>
        <Row label="推理步数">
          <select className={sel} value={picked(STEPS_OPTIONS, v.steps)} onChange={(e) => set('steps', e.target.value)}>
            <option value="">{EMPTY_LABEL}</option>
            {STEPS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Row>
        <Row label="引导强度 CFG">
          <select className={sel} value={picked(CFG_OPTIONS, v.cfg)} onChange={(e) => set('cfg', e.target.value)}>
            <option value="">{EMPTY_LABEL}</option>
            {CFG_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Row>
        <Row label="Turbo 模式">
          <select className={sel} value={picked(TURBO_OPTIONS, v.turbo)} onChange={(e) => set('turbo', e.target.value)}>
            <option value="">{EMPTY_LABEL}</option>
            {TURBO_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Row>
      </div>
      <div className="mt-2.5">
        <label className="block text-xs text-slate-500 mb-1">
          种子 Seed
          <span className="text-slate-400 font-normal">（留空 = 随机；批量统一设置时全片风格一致）</span>
        </label>
        <input
          type="number"
          className="input-base text-sm"
          value={v.seed == null ? '' : String(v.seed)}
          onChange={(e) => set('seed', e.target.value)}
          placeholder="留空 = 随机"
        />
      </div>
    </div>
  )
}
