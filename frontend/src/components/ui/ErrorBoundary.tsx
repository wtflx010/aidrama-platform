import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** 出错后的回退渲染；不传则显示通用错误卡片 + 刷新按钮 */
  fallback?: ReactNode
}
interface State {
  error: Error | null
}

/**
 * 全局错误边界：任何子树渲染异常都会走到这里，
 * 显示可见的「页面出错了」卡片并支持刷新，避免 React 卸载整树导致白屏。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: unknown) {
    console.error('[ErrorBoundary]', error, info)
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div className="min-h-[60vh] flex items-center justify-center p-6">
          <div className="max-w-md w-full rounded-2xl border border-rose-200 bg-rose-50/60 p-6 text-center">
            <div className="text-2xl mb-2">⚠️</div>
            <h2 className="text-base font-semibold text-slate-900 mb-1">页面出错了</h2>
            <p className="text-sm text-slate-500 mb-4 break-all">
              {this.state.error.message || String(this.state.error)}
            </p>
            <button
              onClick={() => {
                this.setState({ error: null })
                window.location.reload()
              }}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white bg-brand-600 hover:bg-brand-700 rounded-lg transition-colors"
            >
              刷新页面
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
