import {Icon} from '../../lib/icons'
import {WELCOME_MSG, SUGGESTIONS} from './shared'


export function WelcomePanel({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="pt-10 pb-4 animate-fade-in">
      <div className="flex flex-col items-center text-center">
        <div className="relative mb-5">
          <div className="absolute inset-0 bg-brand-500/25 blur-2xl rounded-full" />
          <div className="relative w-14 h-14 rounded-2xl bg-gradient-brand shadow-glow-sm flex items-center justify-center">
            <Icon name="clapperboard" size={26} className="text-white" />
          </div>
        </div>
        <h2 className="text-xl font-bold text-slate-900 mb-1.5">创作助手</h2>
        <p className="text-sm text-slate-400 max-w-md leading-relaxed whitespace-pre-line text-left">{WELCOME_MSG}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mt-7 max-w-xl mx-auto">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.title}
            type="button"
            onClick={() => onPick(s.prompt)}
            className="group flex items-start gap-3 text-left rounded-xl border border-slate-200 bg-white px-3.5 py-3 hover:border-brand-300 hover:bg-brand-50/40 hover:shadow-sm transition-all"
          >
            <span className="w-8 h-8 rounded-lg bg-brand-50 border border-brand-100 flex items-center justify-center shrink-0 group-hover:bg-brand-100 transition-colors">
              <Icon name={s.icon} size={15} className="text-brand-600" />
            </span>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium text-slate-700 group-hover:text-brand-700 transition-colors">{s.title}</span>
              <span className="block text-xs text-slate-400 mt-0.5 truncate">{s.desc}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

