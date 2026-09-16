/**
 * 成片导出（全局入口）：按项目 → 幕 选择并导出/下载成片。
 * 由原项目详情页「成片导出」页签升级为全局功能（2026-08-23）。
 */
import { useSearchParams } from 'react-router-dom'
import { ExportPanel } from '../components/ExportPanel'

export default function ExportPage() {
  const [params] = useSearchParams()
  const projectParam = params.get('project')
  return (
    <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6">
      <ExportPanel projectId={projectParam ?? undefined} />
    </div>
  )
}
