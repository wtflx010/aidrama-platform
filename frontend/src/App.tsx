import { useEffect, useState } from 'react'
import { Routes, Route, Link, NavLink } from 'react-router-dom'
import Home from './pages/Home'
import ProjectWorkspace from './pages/ProjectWorkspace'
import AdminModels from './pages/AdminModels'
import Novels from './pages/Novels'
import ScriptDetail from './pages/ScriptDetail'
import VideoLab from './components/VideoLab'
import Assistant from './components/Assistant'
import CanvasBoard from './pages/CanvasBoard'
import AssetLibraryPage from './pages/AssetLibraryPage'
import AssetDetailPage from './pages/AssetDetailPage'
import AudioLibraryPage from './pages/AudioLibraryPage'
import ExportPage from './pages/ExportPage'
import { Icon } from './lib/icons'
import { cn } from './lib/cn'

const NAV_ITEMS = [
  { to: '/assistant', label: '智能体', icon: 'sparkles', end: false },
  { to: '/novels', label: '剧本', icon: 'book', end: false },
  { to: '/', label: '项目', icon: 'folder', end: true },
  { to: '/assets', label: '美术', icon: 'layers', end: false },
  { to: '/audio', label: '音频', icon: 'music', end: false },
  { to: '/exports', label: '导出', icon: 'download', end: false },
  { to: '/canvas', label: '画布', icon: 'grid', end: false },
  { to: '/videolab', label: '视频生成', icon: 'video', end: false },
  { to: '/admin/models', label: '模型管理', icon: 'settings', end: false },
]

/** 轮询后端健康状态（/api/health/ready，免鉴权），每 30s 刷新；替代原先静态"服务在线"假灯。 */
function useBackendHealth(): 'checking' | 'online' | 'offline' {
  const [state, setState] = useState<'checking' | 'online' | 'offline'>('checking')
  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const res = await fetch('/api/health/ready')
        const body = await res.json()
        if (cancelled) return
        setState(res.ok && body.db === 'ok' && body.redis === 'ok' ? 'online' : 'offline')
      } catch {
        if (!cancelled) setState('offline')
      }
    }
    check()
    const id = setInterval(check, 30_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])
  return state
}

export default function App() {
  const health = useBackendHealth()
  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-40 glass-strong border-b border-slate-200">
        <div className="max-w-[1600px] mx-auto px-4 sm:px-6 h-14 flex items-center gap-4 lg:gap-8">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2.5 group shrink-0">
            <div className="relative">
              <div className="absolute inset-0 bg-brand-500/30 blur-md rounded-lg group-hover:bg-brand-500/40 transition-colors" />
              <div className="relative w-8 h-8 rounded-lg bg-gradient-brand flex items-center justify-center shadow-glow-sm">
                <Icon name="clapperboard" size={18} className="text-white" />
              </div>
            </div>
            <span className="text-base font-bold tracking-tight">
              AI <span className="text-gradient">短剧</span>
            </span>
          </Link>

          {/* Nav */}
          <nav className="flex items-center gap-0.5 lg:gap-1 overflow-x-auto no-scrollbar">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end}>
                {({ isActive }) => (
                  <span
                    className={cn(
                      'flex items-center gap-2 px-2.5 lg:px-3 py-1.5 rounded-lg text-sm font-medium transition-all whitespace-nowrap',
                      isActive
                        ? 'bg-brand-500/10 text-brand-600'
                        : 'text-slate-500 hover:text-slate-900 hover:bg-slate-100',
                    )}
                  >
                    <Icon name={item.icon} size={15} />
                    <span className="hidden sm:inline">{item.label}</span>
                  </span>
                )}
              </NavLink>
            ))}
          </nav>

          <div className="flex-1" />

          {/* 右侧：后端健康状态（真实轮询，非静态假灯） */}
          <div className="hidden md:flex items-center gap-2 text-xs text-slate-400 shrink-0">
            <span
              className={cn(
                'w-2 h-2 rounded-full',
                health === 'online'
                  ? 'bg-emerald-400 animate-pulse-glow'
                  : health === 'checking'
                    ? 'bg-amber-400'
                    : 'bg-rose-500',
              )}
            />
            <span>
              {health === 'online' ? '服务在线' : health === 'checking' ? '连接中…' : '服务离线'}
            </span>
          </div>
        </div>
      </header>

      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/assets" element={<AssetLibraryPage />} />
          <Route path="/assets/:id" element={<AssetDetailPage />} />
          <Route path="/audio" element={<AudioLibraryPage />} />
          <Route path="/exports" element={<ExportPage />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/videolab" element={<VideoLab />} />
          <Route path="/canvas" element={<CanvasBoard />} />
          <Route path="/novels" element={<Novels />} />
          <Route path="/novels/:id" element={<ScriptDetail />} />
          <Route path="/projects/:id" element={<ProjectWorkspace />} />
          <Route path="/admin/models" element={<AdminModels />} />
        </Routes>
      </main>
    </div>
  )
}
