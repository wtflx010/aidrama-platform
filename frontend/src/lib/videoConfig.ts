/**
 * 视频生成档位统一配置（前端唯一事实来源）。
 *
 * 严格对齐后端两份定义，改档位只改这里 + 后端，避免各处各自硬编码导致漂移：
 * - 后端项目分辨率尺寸：`backend/app/services/video_service.py::_RESOLUTION_DIMS` / `VIDEO_RESOLUTIONS`
 * - 后端 H3 生成档位：`backend/app/providers/comfyui.py::_MMAX_RES_GRADES`
 *
 * 铁律：
 * - H3 可直接生成的档位：'0.1mp'~'1.0mp' 级联档 + '480p' | '768p'（均为 32 倍数，H3 latent patch 对齐）。
 *   '0.5mp'(960×544) 官方 16GB 低显存基准；'0.7mp'(1152×640) **官方主推 recipe**，融合模型默认档。
 *   新增 0.1/0.2/0.25/0.3/0.4/0.6/0.8/0.9/1.0mp 为 32 倍数对齐的合法 latent 档（非官方精调 recipe）。
 * - 480p/720p/768p 已从可选项移除（0.4MP·832×480≈480P、1.0MP·1344×768≈768P，与 MP 级联重复；720P 为 32 倍数兼容回退档）；后端仍保留其 legacy 分辨率值供老项目兼容，不在新档位下拉提供。
 * - '1080p' 只是「超分产物」档位（upscale_service 输出），**绝不能加进生成下拉**——
 *   后端 normalize_resolution 会把未知生成档静默回退 0.7mp，选了 1080p 反而悄悄降档。
 */
import type { AspectRatio } from '../api/types'

export type GenResolution =
  | '0.1mp' | '0.2mp' | '0.25mp' | '0.3mp' | '0.4mp' | '0.5mp'
  | '0.6mp' | '0.7mp' | '0.8mp' | '0.9mp' | '1.0mp'

export const GEN_RESOLUTIONS: readonly GenResolution[] = [
  '0.1mp', '0.2mp', '0.25mp', '0.3mp', '0.4mp', '0.5mp', '0.6mp', '0.7mp', '0.8mp', '0.9mp', '1.0mp',
]

export const DEFAULT_GEN_RESOLUTION: GenResolution = '0.7mp'

export interface GenResolutionMeta {
  value: GenResolution
  /** 短名（如 480P / 768P） */
  label: string
  /** 各画面比例下的像素尺寸（与后端 _RESOLUTION_DIMS 严格一致；H3 latent 32 倍数对齐） */
  dims: Record<AspectRatio, string>
  desc?: string
}

export const GEN_RESOLUTION_META: Record<GenResolution, GenResolutionMeta> = {
  '0.1mp': {
    value: '0.1mp',
    label: '0.1MP',
    dims: { '16:9': '416×224', '9:16': '224×416', '4:3': '352×256', '3:4': '256×352', '1:1': '320×320' },
    desc: '0.1MP 档（H3 32 倍数对齐）',
  },
  '0.2mp': {
    value: '0.2mp',
    label: '0.2MP',
    dims: { '16:9': '576×320', '9:16': '320×576', '4:3': '480×384', '3:4': '384×480', '1:1': '416×416' },
    desc: '0.2MP 档（H3 32 倍数对齐）',
  },
  '0.25mp': {
    value: '0.25mp',
    label: '0.25MP',
    dims: { '16:9': '640×352', '9:16': '352×640', '4:3': '544×416', '3:4': '416×544', '1:1': '480×480' },
    desc: '0.25MP 档（H3 32 倍数对齐）',
  },
  '0.3mp': {
    value: '0.3mp',
    label: '0.3MP',
    dims: { '16:9': '704×384', '9:16': '384×704', '4:3': '608×448', '3:4': '448×608', '1:1': '512×512' },
    desc: '0.3MP 档（H3 32 倍数对齐）',
  },
  '0.4mp': {
    value: '0.4mp',
    label: '0.4MP',
    dims: { '16:9': '832×480', '9:16': '480×832', '4:3': '736×544', '3:4': '544×736', '1:1': '640×640' },
    desc: '0.4MP 档（H3 32 倍数对齐）',
  },
  '0.6mp': {
    value: '0.6mp',
    label: '0.6MP',
    dims: { '16:9': '1088×608', '9:16': '608×1088', '4:3': '928×704', '3:4': '704×928', '1:1': '800×800' },
    desc: '0.6MP 档（H3 32 倍数对齐）',
  },
  '0.8mp': {
    value: '0.8mp',
    label: '0.8MP',
    dims: { '16:9': '1216×672', '9:16': '672×1216', '4:3': '1056×768', '3:4': '768×1056', '1:1': '896×896' },
    desc: '0.8MP 档（H3 32 倍数对齐）',
  },
  '0.9mp': {
    value: '0.9mp',
    label: '0.9MP',
    dims: { '16:9': '1280×704', '9:16': '704×1280', '4:3': '1088×832', '3:4': '832×1088', '1:1': '960×960' },
    desc: '0.9MP 档（H3 32 倍数对齐）',
  },
  '1.0mp': {
    value: '1.0mp',
    label: '1.0MP',
    dims: { '16:9': '1344×768', '9:16': '768×1344', '4:3': '1184×864', '3:4': '864×1184', '1:1': '1024×1024' },
    desc: '1.0MP 档（H3 32 倍数对齐）',
  },
  '0.5mp': {
    value: '0.5mp',
    label: '0.5MP',
    dims: { '16:9': '960×544', '9:16': '544×960', '4:3': '704×528', '3:4': '528×704', '1:1': '512×512' },
    desc: '官方 16GB 低显存基准（更稳、更快）',
  },
  '0.7mp': {
    value: '0.7mp',
    label: '0.7MP',
    dims: { '16:9': '1152×640', '9:16': '640×1152', '4:3': '768×576', '3:4': '576×768', '1:1': '672×672' },
    desc: '官方主推档（模型本命尺寸，画质/速度均衡）',
  },

}

export function isGenResolution(v: unknown): v is GenResolution {
  return typeof v === 'string' && (GEN_RESOLUTIONS as readonly string[]).includes(v)
}

/** 某档位 + 画面比例 → 像素尺寸文案；未知档位回退默认档避免标错。 */
export function dimsText(res: unknown, ratio = '16:9'): string {
  const r = isGenResolution(res) ? res : DEFAULT_GEN_RESOLUTION
  return GEN_RESOLUTION_META[r].dims[(ratio || '16:9') as AspectRatio] ?? GEN_RESOLUTION_META[r].dims['16:9']
}

/** 清晰度下拉选项（默认带 16:9 尺寸标注，一眼对齐档位）。 */
export function genResolutionOptions(withDims = true) {
  return GEN_RESOLUTIONS.map((r) => ({
    value: r,
    label: withDims ? `${GEN_RESOLUTION_META[r].label}·${dimsText(r, '16:9')}` : GEN_RESOLUTION_META[r].label,
  }))
}

/** 画面比例下拉选项：按当前清晰度档位动态标尺寸；未知/跟随默认时只显示比例，避免标错。 */
export function aspectRatioOptions(res: unknown) {
  const ratios: readonly AspectRatio[] = ['16:9', '9:16', '4:3', '3:4', '1:1']
  return ratios.map((r) => ({
    value: r,
    label: isGenResolution(res) ? `${r}（${dimsText(res, r)}）` : r,
  }))
}
