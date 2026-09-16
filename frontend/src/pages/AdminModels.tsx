import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Model } from '../api/types'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { Modal } from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import { useConfirm } from '../components/ui/ConfirmDialog'
import { Icon } from '../lib/icons'

const PROVIDER_TYPES = ['openai_compatible', 'http_poll', 'openai_tts', 'comfyui']
const MODEL_TYPES = ['text', 'image', 'video', 'tts']

type Tab = 'text' | 'image' | 'video'

interface QuickVendor {
  key: string
  name: string
  endpoint: string
  model_id: string
  provider_name: string
  scene_codes: string[]
  hint: string
  env_default: string
}

const QUICK_VENDORS: QuickVendor[] = [
  { key: 'deepseek', name: 'DeepSeek', endpoint: 'https://api.deepseek.com/v1', model_id: 'deepseek-chat', provider_name: 'DeepSeek', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'DeepSeek 开放平台（platform.deepseek.com）API Key', env_default: 'DEEPSEEK_API_KEY' },
  { key: 'openai', name: 'OpenAI', endpoint: 'https://api.openai.com/v1', model_id: 'gpt-4o-mini', provider_name: 'OpenAI', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'OpenAI（platform.openai.com）API Key', env_default: 'OPENAI_API_KEY' },
  { key: 'kimi', name: 'Kimi（月之暗面）', endpoint: 'https://api.moonshot.cn/v1', model_id: 'moonshot-v1-8k', provider_name: 'Moonshot', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'Moonshot（platform.moonshot.cn）API Key', env_default: 'MOONSHOT_API_KEY' },
  { key: 'qwen', name: '通义千问（阿里云）', endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model_id: 'qwen-plus', provider_name: 'Aliyun DashScope', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'DashScope 百炼（bailian.console.aliyun.com）API Key', env_default: 'DASHSCOPE_API_KEY' },
  { key: 'glm', name: '智谱 GLM', endpoint: 'https://open.bigmodel.cn/api/paas/v4', model_id: 'glm-4-flash', provider_name: 'ZhipuAI', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: '智谱开放平台（open.bigmodel.cn）API Key', env_default: 'ZHIPU_API_KEY' },
  { key: 'volcengine', name: '火山方舟（豆包）', endpoint: 'https://ark.cn-beijing.volces.com/api/v3', model_id: 'doubao-pro-32k', provider_name: 'Volcengine Ark', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: '火山方舟（console.volcengine.com/ark）API Key', env_default: 'ARK_API_KEY' },
  { key: 'minimax', name: 'MiniMax', endpoint: 'https://api.minimax.chat/v1', model_id: 'abab6.5s-chat', provider_name: 'MiniMax', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'MiniMax 开放平台（platform.minimaxi.com）API Key', env_default: 'MINIMAX_API_KEY' },
  // ── 编程向 / Agent 向（Codex / Claude / OpenCode / 国内编程模型）──
  { key: 'codex', name: 'OpenAI Codex', endpoint: 'https://api.openai.com/v1', model_id: 'gpt-5-codex', provider_name: 'OpenAI', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'OpenAI Codex（同 OpenAI 平台 Key，可用 gpt-5-codex / codex-mini-latest）', env_default: 'CODEX_API_KEY' },
  { key: 'claude', name: 'Anthropic Claude', endpoint: 'https://api.anthropic.com/v1', model_id: 'claude-sonnet-4-20250514', provider_name: 'Anthropic', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'Claude Code 同款模型；官方为 Messages API，需经 OpenAI 兼容网关/代理使用则填对应 endpoint 与 Key', env_default: 'ANTHROPIC_API_KEY' },
  { key: 'opencode', name: 'OpenCode（编码 Agent）', endpoint: 'https://api.opencode.ai/v1', model_id: 'default', provider_name: 'OpenCode', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'OpenCode 是开源本地编码 Agent，不提供自身模型 API——它底层调用 Claude/OpenAI/Gemini 等模型；建议改选上方 Claude / Codex / Gemini 模板完成接入', env_default: 'OPENCODE_API_KEY' },
  { key: 'gemini', name: 'Google Gemini', endpoint: 'https://generativelanguage.googleapis.com/v1beta/openai', model_id: 'gemini-2.0-flash', provider_name: 'Google Gemini', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'Gemini API Key（aistudio.google.com）——端点已用官方 OpenAI 兼容路径', env_default: 'GEMINI_API_KEY' },
  { key: 'kimi-k2', name: 'Kimi K2（编程）', endpoint: 'https://api.moonshot.cn/v1', model_id: 'kimi-k2-0711-preview', provider_name: 'Moonshot', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'Kimi K2 编程模型（同 Moonshot 平台 Key）', env_default: 'MOONSHOT_API_KEY' },
  { key: 'qwen-coder', name: '通义 qwen-coder（编程）', endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model_id: 'qwen3-coder-plus', provider_name: 'Aliyun DashScope', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: '通义灵码同源编程模型（DashScope 百炼 Key）', env_default: 'DASHSCOPE_API_KEY' },
  { key: 'codegeex', name: 'CodeGeeX（编程）', endpoint: 'https://open.bigmodel.cn/api/paas/v4', model_id: 'codegeex-4', provider_name: 'ZhipuAI', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: '智谱 CodeGeeX 编程模型（同智谱开放平台 Key）', env_default: 'ZHIPU_API_KEY' },
  { key: 'doubao-coder', name: '豆包 Seed-Code（编程）', endpoint: 'https://ark.cn-beijing.volces.com/api/v3', model_id: 'doubao-seed-code', provider_name: 'Volcengine Ark', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: '火山方舟豆包编程模型（同 ark Key，创建后可在列表改模型 ID）', env_default: 'ARK_API_KEY' },
  { key: 'deepseek-coder', name: 'DeepSeek-Coder（编程）', endpoint: 'https://api.deepseek.com/v1', model_id: 'deepseek-coder', provider_name: 'DeepSeek', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: 'DeepSeek 编程模型（同 DeepSeek 平台 Key）', env_default: 'DEEPSEEK_API_KEY' },
  { key: 'hunyuan', name: '腾讯混元', endpoint: 'https://api.hunyuan.cloud.tencent.com/v1', model_id: 'hunyuan-turbos-latest', provider_name: 'Tencent Hunyuan', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: '腾讯云混元大模型（腾讯云 API Key）', env_default: 'HUNYUAN_API_KEY' },
  { key: 'spark', name: '讯飞星火', endpoint: 'https://spark-api-open.xf-yun.com/v1', model_id: '4.0Ultra', provider_name: 'iFlytek Spark', scene_codes: ['script', 'storyboard', 'expand', 'subtitle', 'voice_recommend'], hint: '讯飞星火开放平台（xf-yun.com）API Key', env_default: 'SPARK_API_KEY' },
]

/** 主流视频生成模型预设（异步提交 + 轮询，走 http_poll provider；各家请求体略异，创建后按需微调） */
interface QuickVideoVendor {
  key: string
  name: string
  endpoint: string
  model_id: string
  provider_name: string
  hint: string
  env_default: string
  protocol: string
}

const QUICK_VIDEO_VENDORS: QuickVideoVendor[] = [
  { key: 'sora', name: 'OpenAI Sora', endpoint: 'https://api.openai.com/v1', model_id: 'sora-2', provider_name: 'OpenAI', protocol: 'OpenAI 视频 API（异步提交+轮询，需 body_template）', hint: 'OpenAI 官方 Sora 视频 Key', env_default: 'SORA_API_KEY' },
  { key: 'veo', name: 'Google Veo', endpoint: 'https://generativelanguage.googleapis.com/v1beta', model_id: 'veo-3.0-generate-001', provider_name: 'Google Gemini', protocol: 'Gemini 官方视频 API（async 任务）', hint: 'Google AI Studio 视频 Key', env_default: 'VEO_API_KEY' },
  { key: 'kling', name: '快手可灵 Kling', endpoint: 'https://api-singapore.klingai.com/v1', model_id: 'kling-v1', provider_name: 'KlingAI', protocol: '可灵 OpenAI 兼容异步接口', hint: '可灵开放平台（klingai.com）API Key', env_default: 'KLING_API_KEY' },
  { key: 'runway', name: 'Runway Gen', endpoint: 'https://api.dev.runwayml.com/v1', model_id: 'gen4_turbo', provider_name: 'Runway', protocol: 'Runway 官方 API（任务提交）', hint: 'Runway 开发者平台 Key', env_default: 'RUNWAY_API_KEY' },
  { key: 'luma', name: 'Luma Dream Machine', endpoint: 'https://api.lumalabs.ai/dream-machine/v1', model_id: 'ray-2', provider_name: 'Luma AI', protocol: 'Luma 官方视频 API（任务提交）', hint: 'Luma AI 平台 Key', env_default: 'LUMA_API_KEY' },
  { key: 'wan', name: '通义万相 Wan（阿里）', endpoint: 'https://dashscope.aliyuncs.com/api/v1', model_id: 'wan2.2-t2v', provider_name: 'Aliyun DashScope', protocol: 'DashScope 视频提交接口（http_poll）', hint: 'DashScope 百炼 Key（视频生成需开通）', env_default: 'DASHSCOPE_API_KEY' },
  { key: 'cogvideox', name: '智谱清影 CogVideoX', endpoint: 'https://open.bigmodel.cn/api/paas/v4', model_id: 'cogvideox', provider_name: 'ZhipuAI', protocol: '智谱 CogVideoX 视频接口', hint: '智谱开放平台 Key（清影视频）', env_default: 'ZHIPU_API_KEY' },
  { key: 'seedance', name: '豆包 Seedance（火山）', endpoint: 'https://ark.cn-beijing.volces.com/api/v3', model_id: 'doubao-seedance-1-0-pro', provider_name: 'Volcengine Ark', protocol: '火山方舟视频提交接口', hint: '火山方舟 Key（Seedance 视频）', env_default: 'ARK_API_KEY' },
  { key: 'hunyuan-video', name: '腾讯混元视频', endpoint: 'https://api.hunyuan.cloud.tencent.com/v1', model_id: 'hunyuan-video-v1', provider_name: 'Tencent Hunyuan', protocol: '混元视频异步接口', hint: '腾讯云混元视频 Key', env_default: 'HUNYUAN_API_KEY' },
  { key: 'step', name: '阶跃星辰 Step-Video', endpoint: 'https://api.stepfun.com/v1', model_id: 'step-video-v1', provider_name: 'StepFun', protocol: '阶跃 OpenAI 兼容视频接口', hint: '阶跃星辰开放平台 Key', env_default: 'STEP_API_KEY' },
]

/** 主流生图 AP（OpenAI 兼容 /images/generations，经后端 openai_compatible 适配器驱动） */
interface QuickImageVendor {
  key: string
  name: string
  endpoint: string
  model_id: string
  provider_name: string
  hint: string
  env_default: string
  scene_codes: string[]
  sort: number
}

const QUICK_IMAGE_VENDORS: QuickImageVendor[] = [
  { key: 'gpt-image', name: 'OpenAI · DALL·E / gpt-image', endpoint: 'https://api.openai.com/v1', model_id: 'gpt-image-1', provider_name: 'OpenAI', hint: 'OpenAI（platform.openai.com）生图 Key；也可填 dall-e-3', env_default: 'OPENAI_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 50 },
  { key: 'wanx', name: '通义万相 Wan（阿里）', endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model_id: 'wanx2.1-t2i-turbo', provider_name: 'Aliyun DashScope', hint: 'DashScope 百炼（bailian.console.aliyun.com）Key', env_default: 'DASHSCOPE_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 50 },
  { key: 'cogview', name: '智谱 CogView', endpoint: 'https://open.bigmodel.cn/api/paas/v4', model_id: 'cogview-3-flash', provider_name: 'ZhipuAI', hint: '智谱开放平台（open.bigmodel.cn）Key', env_default: 'ZHIPU_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 50 },
  { key: 'seedream', name: '火山方舟 · 即梦/Seedream（字节）', endpoint: 'https://ark.cn-beijing.volces.com/api/v3', model_id: 'doubao-seedream-3-0-t2i-250415', provider_name: 'Volcengine Ark', hint: '火山方舟（console.volcengine.com/ark）Key', env_default: 'ARK_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 50 },
  { key: 'siliconflow', name: '硅基流动 SiliconFlow（FLUX/SD）', endpoint: 'https://api.siliconflow.cn/v1', model_id: 'black-forest-labs/FLUX.1-schnell', provider_name: 'SiliconFlow', hint: 'SiliconFlow（cloud.siliconflow.cn）Key', env_default: 'SILICONFLOW_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 50 },
  { key: 'minimax-image', name: 'MiniMax 海螺图像', endpoint: 'https://api.minimax.chat/v1', model_id: 'image-01', provider_name: 'MiniMax', hint: 'MiniMax 开放平台（platform.minimaxi.com）Key', env_default: 'MINIMAX_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 50 },
  { key: 'sensenova', name: '商汤日日新生图', endpoint: 'https://api.sensenova.cn/v1', model_id: 'Nova-1-latest', provider_name: 'SenseNova', hint: '商汤大装置（sensenova.cn）Key；模型 ID 未知可在创建后编辑', env_default: 'SENSENOVA_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 50 },
  { key: 'custom-openai', name: '自定义 OpenAI 兼容生图', endpoint: 'https://your-gateway.example.com/v1', model_id: 'your-image-model', provider_name: 'Custom', hint: '任何 OpenAI 兼容生图网关（含中转/代理）；覆盖文心一格、Midjourney 等经网关路由', env_default: 'GEN_IMAGE_API_KEY', scene_codes: ['keyframe', 'character_cover', 'scene', 'prop'], sort: 90 },
]

const isImage = (m: Model) => m.model_type === 'image'
const isVideo = (m: Model) => m.model_type === 'video'
const isText = (m: Model) => !isImage(m) && !isVideo(m)

export default function AdminModels() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const [tab, setTab] = useState<Tab>('text')
  const { data: models = [], isLoading } = useQuery({
    queryKey: ['admin-models'],
    queryFn: () => api.get<Model[]>('/admin/models'),
  })
  const [editing, setEditing] = useState<Model | null>(null)
  const [adding, setAdding] = useState(false)
  const [quickOpen, setQuickOpen] = useState(false)

  const toggle = useMutation({
    mutationFn: (m: Model) => api.post<Model>(`/admin/models/${m.id}/toggle`, { is_enabled: !m.is_enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-models'] }),
  })
  const setDefault = useMutation({
    mutationFn: (id: string) => api.post<Model>(`/admin/models/${id}/set-default`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-models'] }),
  })
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/admin/models/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-models'] }),
  })
  const test = useMutation({
    mutationFn: (id: string) => api.post<{ ok: boolean; message: string }>(`/admin/models/${id}/test`),
    onSuccess: (r) => {
      if (r.ok) toast.success(`测试成功: ${r.message.slice(0, 60)}`)
      else toast.error(`测试失败: ${r.message.slice(0, 60)}`)
    },
  })

  async function handleDelete(m: Model) {
    const ok = await confirm({
      title: '删除模型',
      message: `确定删除模型「${m.name}」？此操作不可撤销。`,
      danger: true,
      confirmText: '删除',
    })
    if (ok) del.mutate(m.id)
  }

  const shown = tab === 'image' ? models.filter(isImage) : tab === 'video' ? models.filter(isVideo) : models.filter(isText)

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8 animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
            <Icon name="settings" size={24} className="text-brand-600" />
            模型管理
          </h1>
          <p className="text-sm text-slate-400 mt-1.5 pl-[34px]">
            {tab === 'text' ? '文本 / API 模型：配置第三方大模型 API Key 与参数' : tab === 'image' ? '生图模型：云端文生图 API 与本地 ComfyUI 出图模型（工作流参数在行内编辑）' : '视频模型：云端视频生成 API 与本地 ComfyUI 视频工作流（工作流参数在行内编辑）'}
          </p>
        </div>
        {tab === 'text' ? (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="md" leftIcon={<Icon name="plus" size={16} />} onClick={() => setQuickOpen((v) => !v)}>
              {quickOpen ? '收起接入' : '接入主流 API'}
            </Button>
            <Button variant="primary" size="md" leftIcon={<Icon name="plus" size={16} />} onClick={() => setAdding(true)}>
              新增模型
            </Button>
          </div>
        ) : tab === 'image' ? (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="md" leftIcon={<Icon name="image" size={16} />} onClick={() => setQuickOpen((v) => !v)}>
              {quickOpen ? '收起接入' : '接入主流生图 API'}
            </Button>
            <Button variant="primary" size="md" leftIcon={<Icon name="plus" size={16} />} onClick={() => setAdding(true)}>
              新增生图模型
            </Button>
          </div>
        ) : tab === 'video' ? (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="md" leftIcon={<Icon name="plus" size={16} />} onClick={() => setQuickOpen((v) => !v)}>
              {quickOpen ? '收起接入' : '接入视频 API'}
            </Button>
            <Button variant="primary" size="md" leftIcon={<Icon name="plus" size={16} />} onClick={() => setAdding(true)}>
              新增视频模型
            </Button>
          </div>
        ) : null}
      </div>

      <div className="flex items-center gap-1 mb-6 p-1 bg-slate-100/80 rounded-xl w-fit">
        <button
          onClick={() => setTab('text')}
          className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-colors ${tab === 'text' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
        >
          <Icon name="type" size={16} />
          文本模型
        </button>
        <button
          onClick={() => setTab('image')}
          className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-colors ${tab === 'image' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
        >
          <Icon name="image" size={16} />
          生图模型
        </button>
        <button
          onClick={() => setTab('video')}
          className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-colors ${tab === 'video' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
        >
          <Icon name="video" size={16} />
          视频模型
        </button>

      </div>

      {quickOpen && tab === 'text' && <QuickAccessPanel onClose={() => setQuickOpen(false)} />}
      {quickOpen && tab === 'image' && <QuickImageAccessPanel models={models} onClose={() => setQuickOpen(false)} />}
      {quickOpen && tab === 'video' && <QuickVideoAccessPanel models={models} onClose={() => setQuickOpen(false)} />}

      <ModelTable
        models={shown}
        isLoading={isLoading}
        onEdit={setEditing}
        onDelete={handleDelete}
        onTest={(m) => test.mutate(m.id)}
        onDefault={(m) => setDefault.mutate(m.id)}
        onToggle={(m) => toggle.mutate(m)}
        isTesting={(id) => test.isPending && test.variables === id}
      />

      {(adding || editing) && (
        <ModelDialog
          model={editing}
          defaultProvider={tab === 'video' ? 'http_poll' : 'openai_compatible'}
          onClose={() => { setAdding(false); setEditing(null) }}
          onSaved={() => { setAdding(false); setEditing(null); qc.invalidateQueries({ queryKey: ['admin-models'] }) }}
        />
      )}
    </div>
  )
}

// ── 快速接入主流 API ────────────────────────────────
function VendorChip({ v, active, onPick }: { v: QuickVendor; active: boolean; onPick: (v: QuickVendor) => void }) {
  return (
    <button
      onClick={() => onPick(v)}
      className={`px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${active ? 'bg-brand-500 text-white border-brand-500' : 'bg-white text-slate-600 border-slate-200 hover:border-brand-400'}`}
    >
      {v.name}
    </button>
  )
}

function QuickAccessPanel({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [vendor, setVendor] = useState<QuickVendor>(QUICK_VENDORS[0])
  const [apiKey, setApiKey] = useState('')
  const [modelId, setModelId] = useState(QUICK_VENDORS[0].model_id)
  const [envName, setEnvName] = useState(QUICK_VENDORS[0].env_default)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function pick(v: QuickVendor) {
    setVendor(v)
    setModelId(v.model_id)
    setEnvName(v.env_default)
    setError(null)
  }

  async function submit() {
    if (!apiKey.trim()) { setError('请填写 API Key'); return }
    if (!modelId.trim()) { setError('请填写模型 ID'); return }
    setBusy(true); setError(null)
    try {
      // 1) 写入 .env（keys 接口）
      await api.post('/admin/models/keys', { env_name: envName.trim(), api_key: apiKey.trim() })
      // 2) 创建模型记录
      await api.post<Model>('/admin/models', {
        name: vendor.name + ' ' + modelId.trim(),
        provider_type: 'openai_compatible',
        model_type: 'text',
        provider_name: vendor.provider_name,
        endpoint: vendor.endpoint,
        api_key_ref: envName.trim(),
        model_id: modelId.trim(),
        capability: {},
        credits_per_unit: 0,
        scene_codes: vendor.scene_codes,
        is_enabled: true,
        is_default: false,
        sort: 50,
        http_poll_config: null,
      })
      toast.success(`已接入 ${vendor.name}`)
      qc.invalidateQueries({ queryKey: ['admin-models'] })
      setApiKey('')
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="mb-6 border-brand-200">
      <div className="px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
            <Icon name="zap" size={16} className="text-brand-600" />
            一键接入主流文本 API
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><Icon name="x" size={16} /></button>
        </div>
        {/* 服务商选择（通用 / 编程&Agent 两组） */}
        <div className="mb-4 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-slate-400 w-16 shrink-0">通用文本</span>
            {QUICK_VENDORS.slice(0, 7).map((v) => (
              <VendorChip key={v.key} v={v} active={vendor.key === v.key} onPick={pick} />
            ))}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-slate-400 w-16 shrink-0">编程 · Agent</span>
            {QUICK_VENDORS.slice(7).map((v) => (
              <VendorChip key={v.key} v={v} active={vendor.key === v.key} onPick={pick} />
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={vendor.hint}
              className="input-base"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">模型 ID（可改）</label>
            <input value={modelId} onChange={(e) => setModelId(e.target.value)} className="input-base" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">环境变量名</label>
            <input value={envName} onChange={(e) => setEnvName(e.target.value)} className="input-base" />
          </div>
        </div>
        {error && (
          <p className="text-xs text-rose-600 mt-3 flex items-center gap-1"><Icon name="alert-circle" size={12} /> {error}</p>
        )}
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button size="sm" onClick={submit} loading={busy}>接入并创建</Button>
        </div>
        <p className="text-xs text-slate-400 mt-3">
          Key 写入 backend/.env 对应环境变量，模型记录自动创建并启用，可在下方列表「测试」连通性。
        </p>
      </div>
    </Card>
  )
}

// ── 快速接入视频 API ────────────────────────────────
function VideoChip({ v, active, onPick }: { v: QuickVideoVendor; active: boolean; onPick: (v: QuickVideoVendor) => void }) {
  return (
    <button
      onClick={() => onPick(v)}
      className={`px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${active ? 'bg-brand-500 text-white border-brand-500' : 'bg-white text-slate-600 border-slate-200 hover:border-brand-400'}`}
    >
      {v.name}
    </button>
  )
}

function QuickVideoAccessPanel({ models, onClose }: { models: Model[]; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [vendor, setVendor] = useState<QuickVideoVendor>(QUICK_VIDEO_VENDORS[0])
  const [apiKey, setApiKey] = useState('')
  const [modelId, setModelId] = useState(QUICK_VIDEO_VENDORS[0].model_id)
  const [envName, setEnvName] = useState(QUICK_VIDEO_VENDORS[0].env_default)
  const [endpoint, setEndpoint] = useState(QUICK_VIDEO_VENDORS[0].endpoint)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hpc, setHpc] = useState('')

  function pick(v: QuickVideoVendor) {
    setVendor(v)
    setModelId(v.model_id)
    setEnvName(v.env_default)
    setEndpoint(v.endpoint)
    setError(null)
  }

  async function submit() {
    if (!apiKey.trim()) { setError('请填写 API Key'); return }
    if (!modelId.trim()) { setError('请填写模型 ID'); return }
    let hpcParsed: object
    try {
      hpcParsed = JSON.parse(hpc || '{}')
    } catch {
      setError('http_poll_config 不是合法 JSON'); return
    }
    if (!(hpcParsed as Record<string, unknown>).submit_path) {
      setError('http_poll_config 必须包含 submit_path'); return
    }
    setBusy(true); setError(null)
    try {
      await api.post('/admin/models/keys', { env_name: envName.trim(), api_key: apiKey.trim() })
      await api.post<Model>('/admin/models', {
        name: vendor.name + ' ' + modelId.trim(),
        provider_type: 'http_poll',
        model_type: 'video',
        provider_name: vendor.provider_name,
        endpoint: endpoint.trim(),
        api_key_ref: envName.trim(),
        model_id: modelId.trim(),
        capability: {},
        credits_per_unit: 0,
        scene_codes: ['video'],
        is_enabled: true,
        is_default: false,
        sort: 60,
        http_poll_config: hpcParsed,
      })
      toast.success(`已接入视频模型 ${vendor.name}`)
      qc.invalidateQueries({ queryKey: ['admin-models'] })
      setApiKey('')
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const comfyuiVideoTemplates = models.filter((m) => m.provider_type === 'comfyui' && m.model_type === 'video')

  return (
    <div className="space-y-4">
    <Card className="border-brand-200">
      <div className="px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
            <Icon name="video" size={16} className="text-brand-600" />
            一键接入主流视频 API
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><Icon name="x" size={16} /></button>
        </div>
        <div className="flex flex-wrap gap-2 mb-4">
          {QUICK_VIDEO_VENDORS.map((v) => (
            <VideoChip key={v.key} v={v} active={vendor.key === v.key} onPick={pick} />
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">API Key</label>
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={vendor.hint} className="input-base" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">模型 ID（可改）</label>
            <input value={modelId} onChange={(e) => setModelId(e.target.value)} className="input-base" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">Endpoint（可改）</label>
            <input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} className="input-base" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1.5">环境变量名</label>
            <input value={envName} onChange={(e) => setEnvName(e.target.value)} className="input-base" />
          </div>
        </div>
        <div className="mt-3">
          <label className="block text-xs text-slate-500 mb-1.5">http_poll_config (JSON，必填 submit_path)</label>
          <textarea value={hpc} onChange={(e) => setHpc(e.target.value)} rows={4}
            placeholder={'{\n  "submit_path": "/videos",\n  "query_path": "/videos/{task_id}",\n  "body_template": "{...}"\n}'}
            className="input-base font-mono text-xs" />
        </div>
        <p className="text-xs text-slate-400 mt-2 flex items-start gap-1">
          <Icon name="info" size={12} className="mt-0.5 shrink-0" />
          协议：{vendor.protocol}。各家视频 API 请求体字段不同，请按官方文档在此填写 submit_path / query_path / body_template（提交校验 submit_path 必填，避免空配置落库）。
        </p>
        {error && (
          <p className="text-xs text-rose-600 mt-3 flex items-center gap-1"><Icon name="alert-circle" size={12} /> {error}</p>
        )}
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button size="sm" onClick={submit} loading={busy}>接入并创建</Button>
        </div>
      </div>
    </Card>

    <Card className="border-brand-200">
      <div className="px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
            <Icon name="zap" size={16} className="text-brand-600" />
            添加本地 ComfyUI 生视频模型
          </h3>
        </div>
        <ComfyUIAddForm modelType="video" templates={comfyuiVideoTemplates} onDone={() => qc.invalidateQueries({ queryKey: ['admin-models'] })} />
      </div>
    </Card>
    </div>
  )
}


// ── 快速接入生图 API ───────────────────────────────
function ImageVendorChip({ v, active, onPick }: { v: QuickImageVendor; active: boolean; onPick: (v: QuickImageVendor) => void }) {
  return (
    <button
      onClick={() => onPick(v)}
      className={'px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ' + (active ? 'bg-brand-500 text-white border-brand-500' : 'bg-white text-slate-600 border-slate-200 hover:border-brand-400')}
    >
      {v.name}
    </button>
  )
}

function QuickImageAccessPanel({ models, onClose }: { models: Model[]; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [vendor, setVendor] = useState<QuickImageVendor>(QUICK_IMAGE_VENDORS[0])
  const [apiKey, setApiKey] = useState('')
  const [modelId, setModelId] = useState(QUICK_IMAGE_VENDORS[0].model_id)
  const [endpoint, setEndpoint] = useState(QUICK_IMAGE_VENDORS[0].endpoint)
  const [envName, setEnvName] = useState(QUICK_IMAGE_VENDORS[0].env_default)
  const [capability, setCapability] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function pick(v: QuickImageVendor) {
    setVendor(v)
    setModelId(v.model_id)
    setEndpoint(v.endpoint)
    setEnvName(v.env_default)
    setError(null)
  }

  async function submit() {
    if (!apiKey.trim()) { setError('请填写 API Key'); return }
    if (!modelId.trim()) { setError('请填写模型 ID'); return }
    setBusy(true); setError(null)
    try {
      let cap: object = {}
      if (capability.trim()) {
        try { cap = JSON.parse(capability) } catch { throw new Error('capability 不是合法 JSON') }
      }
      await api.post('/admin/models/keys', { env_name: envName.trim(), api_key: apiKey.trim() })
      await api.post<Model>('/admin/models', {
        name: vendor.name + ' ' + modelId.trim(),
        provider_type: 'openai_compatible',
        model_type: 'image',
        provider_name: vendor.provider_name,
        endpoint: endpoint.trim(),
        api_key_ref: envName.trim(),
        model_id: modelId.trim(),
        capability: cap,
        credits_per_unit: 0,
        scene_codes: vendor.scene_codes,
        is_enabled: true,
        is_default: false,
        sort: vendor.sort,
        http_poll_config: null,
      })
      toast.success('已接入生图模型 ' + vendor.name)
      qc.invalidateQueries({ queryKey: ['admin-models'] })
      setApiKey('')
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const comfyuiTemplates = models.filter((m) => m.provider_type === 'comfyui' && m.model_type === 'image')

  return (
    <div className="space-y-4">
      <Card className="border-brand-200">
        <div className="px-5 py-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
              <Icon name="image" size={16} className="text-brand-600" />
              一键接入主流生图 API
            </h3>
            <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><Icon name="x" size={16} /></button>
          </div>
          <div className="flex flex-wrap gap-2 mb-4">
            {QUICK_IMAGE_VENDORS.map((v) => (
              <ImageVendorChip key={v.key} v={v} active={vendor.key === v.key} onPick={pick} />
            ))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">API Key</label>
              <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={vendor.hint} className="input-base" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">模型 ID（可改）</label>
              <input value={modelId} onChange={(e) => setModelId(e.target.value)} className="input-base" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">Endpoint（可改）</label>
              <input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} className="input-base" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1.5">环境变量名</label>
              <input value={envName} onChange={(e) => setEnvName(e.target.value)} className="input-base" />
            </div>
          </div>
          <div className="mt-3">
            <label className="block text-xs text-slate-500 mb-1.5">capability (JSON, 可空；如通义万相可填 size=1024*1024)</label>
            <textarea value={capability} onChange={(e) => setCapability(e.target.value)} rows={2} placeholder={'{"size": "1024*1024"}'} className="input-base font-mono text-xs" />
          </div>
          <p className="text-xs text-slate-400 mt-2 flex items-start gap-1">
            <Icon name="info" size={12} className="mt-0.5 shrink-0" />
            以上均为 OpenAI 兼容 /images/generations 接口（经后端 openai_compatible 适配器驱动）。
            部分服务（DALL·E 3 / gpt-image-1 等）返回 base64，后端会自动落盘为本地图片。
          </p>
          {error && (
            <p className="text-xs text-rose-600 mt-3 flex items-center gap-1"><Icon name="alert-circle" size={12} /> {error}</p>
          )}
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
            <Button size="sm" onClick={submit} loading={busy}>接入并创建</Button>
          </div>
        </div>
      </Card>

      <Card className="border-brand-200">
        <div className="px-5 py-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
              <Icon name="zap" size={16} className="text-brand-600" />
              添加本地 ComfyUI 生图模型
            </h3>
          </div>
          <ComfyUIAddForm modelType="image" templates={comfyuiTemplates} onDone={() => qc.invalidateQueries({ queryKey: ['admin-models'] })} />
        </div>
      </Card>
    </div>
  )
}

function ComfyUIAddForm({ modelType, templates, onDone }: { modelType: 'image' | 'video'; templates: Model[]; onDone: () => void }) {
  const toast = useToast()
  const [idx, setIdx] = useState(0)
  const [name, setName] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [modelId, setModelId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const tpl = templates[idx]

  function syncFrom(t: Model | undefined, ep: string) {
    if (!t) return
    setName('')
    setEndpoint(ep || t.endpoint)
    setModelId('')
    setError(null)
  }

  function pick(i: number) {
    setIdx(i)
    syncFrom(templates[i], templates[i]?.endpoint || '')
  }

  if (templates.length === 0) {
    return (
      <p className="text-xs text-slate-400 flex items-start gap-1">
        <Icon name="info" size={12} className="mt-0.5 shrink-0" />
        暂无可克隆的 ComfyUI 模型模板。请先新增一个 ComfyUI 模型后，再克隆它的工作流参数。
      </p>
    )
  }

  async function submit() {
    if (!endpoint.trim()) { setError('请填写 ComfyUI 地址'); return }
    setBusy(true); setError(null)
    try {
      const tid = modelId.trim() || (tpl.model_id + '-copylocal')
      await api.post<Model>('/admin/models', {
        name: name.trim() || ('ComfyUI 生' + (modelType === 'image' ? '图' : '视频') + ' ' + tid),
        provider_type: 'comfyui',
        model_type: modelType,
        provider_name: 'ComfyUI',
        endpoint: endpoint.trim().replace(/\/$/, ''),
        api_key_ref: '',
        model_id: tid,
        capability: { ...(tpl.capability || {}) },
        credits_per_unit: 0,
        scene_codes: [...(tpl.scene_codes || [])],
        is_enabled: true,
        is_default: false,
        sort: tpl.sort + 1,
        http_poll_config: null,
      })
      toast.success('已添加本地 ComfyUI 生' + (modelType === 'image' ? '图' : '视频') + '模型')
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="mb-3">
        <label className="block text-xs text-slate-500 mb-1.5">以现有 ComfyUI 模型为模板（克隆其工作流参数）</label>
        <select value={idx} onChange={(e) => pick(Number(e.target.value))} className="input-base">
          {templates.map((t, i) => (
            <option key={t.id} value={i}>{t.name}（{t.endpoint}）</option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label className="block text-xs text-slate-500 mb-1.5">ComfyUI 地址</label>
          <input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="http://127.0.0.1:8188" className="input-base" />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1.5">模型 ID（可改）</label>
          <input value={modelId} onChange={(e) => setModelId(e.target.value)} placeholder={tpl.model_id + '-copylocal'} className="input-base" />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1.5">显示名称（可改）</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={'ComfyUI 生图 ' + tpl.model_id} className="input-base" />
        </div>
      </div>
      {error && (
        <p className="text-xs text-rose-600 mt-3 flex items-center gap-1"><Icon name="alert-circle" size={12} /> {error}</p>
      )}
      <div className="flex justify-end mt-4">
        <Button size="sm" onClick={submit} loading={busy}>添加并启用</Button>
      </div>
      <p className="text-xs text-slate-400 mt-2 flex items-start gap-1">
        <Icon name="info" size={12} className="mt-0.5 shrink-0" />
        克隆复用模板的 ComfyUI 工作流参数（unet/clip/vae/cfg/steps 等），只需把地址改为你的 ComfyUI 实例。添加后可在列表「测试」连通性。
      </p>
    </div>
  )
}

// ── 模型表格 ────────────────────────────────────────
function ModelTable({
  models,
  isLoading,
  onEdit,
  onDelete,
  onTest,
  onDefault,
  onToggle,
  isTesting,
}: {
  models: Model[]
  isLoading: boolean
  onEdit: (m: Model) => void
  onDelete: (m: Model) => void
  onTest: (m: Model) => void
  onDefault: (m: Model) => void
  onToggle: (m: Model) => void
  isTesting: (id: string) => boolean
}) {
  const cols = ['名称', '类型', 'Provider', '模型ID', 'Key 变量', '场景', '状态', '操作']
  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-white/90 text-slate-500 text-xs uppercase tracking-wider">
            <tr>{cols.map((c) => <th key={c} className="text-left px-4 py-3 font-medium">{c}</th>)}</tr>
          </thead>
          <tbody>
            {isLoading ? (
              [...Array(4)].map((_, i) => (
                <tr key={i} className="border-t border-slate-200">
                  <td className="px-4 py-3"><div className="skeleton h-4 w-32" /></td>
                  <td className="px-4 py-3"><div className="skeleton h-4 w-16" /></td>
                  <td className="px-4 py-3"><div className="skeleton h-4 w-24" /></td>
                  <td className="px-4 py-3"><div className="skeleton h-4 w-28" /></td>
                  <td className="px-4 py-3"><div className="skeleton h-4 w-20" /></td>
                  <td className="px-4 py-3"><div className="skeleton h-5 w-12 rounded-full" /></td>
                </tr>
              ))
            ) : models.length === 0 ? (
              <tr><td colSpan={cols.length} className="px-4 py-10 text-center text-sm text-slate-400">暂无模型，点击右上角新增</td></tr>
            ) : models.map((m) => (
              <tr key={m.id} className="border-t border-slate-200 hover:bg-slate-50 transition-colors">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-900">{m.name}</span>
                    {m.is_default && <Badge variant="purple" size="sm">默认</Badge>}
                  </div>
                </td>
                <td className="px-4 py-3 text-slate-600">{m.model_type}</td>
                <td className="px-4 py-3 text-xs text-slate-400">{m.provider_type}</td>
                <td className="px-4 py-3 text-xs text-slate-400 font-mono break-all min-w-0">{m.model_id}</td>
                <td className="px-4 py-3 text-xs text-slate-400 font-mono">{m.provider_type === 'comfyui' ? '-' : (m.api_key_ref || '-')}</td>
                <td className="px-4 py-3 text-xs text-slate-500 break-all min-w-0">{m.scene_codes.join(',')}</td>
                <td className="px-4 py-3">
                  <Badge variant={m.is_enabled ? 'green' : 'gray'} size="sm" dot>{m.is_enabled ? '启用' : '停用'}</Badge>
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1 flex-wrap">
                    <Button variant="ghost" size="sm" leftIcon={<Icon name="pencil" size={14} />} onClick={() => onEdit(m)}>编辑</Button>
                    <Button variant="ghost" size="sm" leftIcon={<Icon name="zap" size={14} />} loading={isTesting(m.id)} onClick={() => onTest(m)}>测试</Button>
                    <Button variant="ghost" size="sm" leftIcon={<Icon name="check" size={14} />} onClick={() => onDefault(m)}>设默认</Button>
                    <Button variant="ghost" size="sm" leftIcon={<Icon name="refresh" size={14} />} onClick={() => onToggle(m)}>{m.is_enabled ? '停用' : '启用'}</Button>
                    <Button variant="ghost" size="sm" className="text-rose-500 hover:bg-rose-500/10 hover:text-rose-600" leftIcon={<Icon name="trash" size={14} />} onClick={() => onDelete(m)}>删除</Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

interface FormState {
  name: string
  provider_type: string
  model_type: string
  provider_name: string
  endpoint: string
  api_key_ref: string
  model_id: string
  scene_codes: string
  capability: string
  http_poll_config: string
  credits_per_unit: number
  sort: number
  is_enabled: boolean
  is_default: boolean
}

function ModelDialog({
  model,
  defaultProvider,
  onClose,
  onSaved,
}: {
  model: Model | null
  defaultProvider: string
  onClose: () => void
  onSaved: () => void
}) {
  const [f, setF] = useState<FormState>({
    name: model?.name || '',
    provider_type: model?.provider_type || defaultProvider,
    model_type: model?.model_type || 'text',
    provider_name: model?.provider_name || '',
    endpoint: model?.endpoint || '',
    api_key_ref: model?.api_key_ref || '',
    model_id: model?.model_id || '',
    scene_codes: model?.scene_codes.join(',') || '',
    capability: model ? JSON.stringify(model.capability, null, 2) : '{}',
    http_poll_config: model?.http_poll_config ? JSON.stringify(model.http_poll_config, null, 2) : '',
    credits_per_unit: model?.credits_per_unit ?? 0,
    sort: model?.sort ?? 0,
    is_enabled: model?.is_enabled ?? true,
    is_default: model?.is_default ?? false,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof FormState, v: string | number | boolean) => setF({ ...f, [k]: v })

  async function save() {
    setLoading(true); setError(null)
    try {
      let cap: object; let hpc: object | null
      try { cap = JSON.parse(f.capability || '{}') } catch { throw new Error('capability 不是合法 JSON') }
      try { hpc = f.http_poll_config ? JSON.parse(f.http_poll_config) : null } catch { throw new Error('http_poll_config 不是合法 JSON') }
      const body = {
        name: f.name,
        provider_type: f.provider_type,
        model_type: f.model_type,
        provider_name: f.provider_name,
        endpoint: f.endpoint,
        api_key_ref: f.api_key_ref,
        model_id: f.model_id,
        scene_codes: f.scene_codes ? f.scene_codes.split(',').map((s) => s.trim()).filter(Boolean) : [],
        capability: cap,
        http_poll_config: hpc,
        credits_per_unit: Number(f.credits_per_unit),
        sort: Number(f.sort),
        is_enabled: f.is_enabled,
        is_default: f.is_default,
      }
      if (model) await api.put(`/admin/models/${model.id}`, body)
      else await api.post('/admin/models', body)
      onSaved()
    } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }

  const input = (k: keyof FormState, label: string, opts?: { full?: boolean }) => (
    <div className={opts?.full ? 'col-span-2' : ''}>
      <label className="block text-xs text-slate-500 mb-1.5">{label}</label>
      <input value={f[k] as string} onChange={(e) => set(k, e.target.value)} className="input-base" />
    </div>
  )

  return (
    <Modal
      open={true}
      onClose={onClose}
      title={model ? '编辑模型' : '新增模型'}
      size="lg"
      footer={(
        <>
          <Button variant="ghost" size="md" onClick={onClose}>取消</Button>
          <Button variant="primary" size="md" loading={loading} onClick={save}>保存</Button>
        </>
      )}
    >
      <div className="grid grid-cols-2 gap-4">
        {input('name', '名称')}
        <div>
          <label className="block text-xs text-slate-500 mb-1.5">Provider 类型</label>
          <select value={f.provider_type} onChange={(e) => set('provider_type', e.target.value)} className="input-base">
            {PROVIDER_TYPES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1.5">模型类型</label>
          <select value={f.model_type} onChange={(e) => set('model_type', e.target.value)} className="input-base">
            {MODEL_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        {input('provider_name', '厂商名')}
        {input('endpoint', 'Endpoint', { full: true })}
        {input('api_key_ref', '密钥环境变量名(如 AGNES_API_KEY)')}
        {input('model_id', '第三方模型ID')}
        {input('scene_codes', '场景码(逗号分隔, 如 script,keyframe)', { full: true })}
        <div className="col-span-2">
          <label className="block text-xs text-slate-500 mb-1.5">capability (JSON)</label>
          <textarea value={f.capability} onChange={(e) => set('capability', e.target.value)} rows={3} className="input-base font-mono text-xs" />
        </div>
        <div className="col-span-2">
          <label className="block text-xs text-slate-500 mb-1.5">http_poll_config (JSON, http_poll 类型用, 可空)</label>
          <textarea value={f.http_poll_config} onChange={(e) => set('http_poll_config', e.target.value)} rows={3} className="input-base font-mono text-xs" />
        </div>
        {input('credits_per_unit', '积分/单位')}
        {input('sort', '排序')}
        <div className="flex items-center gap-6 col-span-2 pt-2">
          <label className="text-sm text-slate-600 flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={f.is_enabled} onChange={(e) => set('is_enabled', e.target.checked)} className="w-4 h-4 rounded accent-brand-500" /> 启用
          </label>
          <label className="text-sm text-slate-600 flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={f.is_default} onChange={(e) => set('is_default', e.target.checked)} className="w-4 h-4 rounded accent-brand-500" /> 设为默认
          </label>
        </div>
      </div>
      {error && (
        <div className="mt-4 flex items-start gap-2 px-3 py-2.5 rounded-lg bg-rose-500/10 border border-rose-200 text-rose-600 text-sm">
          <Icon name="alert-circle" size={16} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
    </Modal>
  )
}
