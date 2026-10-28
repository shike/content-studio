import { useState } from 'react'
import type { SectionKey } from '../App'
import Topics from './Topics'
import Scripts from './Scripts'
import Digital from './Digital'
import Articles from './Articles'
import Style from './Style'

type Tab = 'topics' | 'content' | 'style'

/** 人设号产线：选题 → 内容看板（脚本/数字人/长文）→ 风格画像。 */
export default function Persona({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [tab, setTab] = useState<Tab>('topics')
  const [sub, setSub] = useState<'scripts' | 'digital' | 'articles'>('scripts')

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">人设号 · 内容产线</h2>
        <span className="text-xs text-slate-400">高质量内容 · 禁一切引导话术 · 风格画像 v7 注入</span>
      </div>
      <div className="mb-3 flex gap-1.5">
        {([
          ['topics', '选题'],
          ['content', '内容看板'],
          ['style', '风格画像'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'topics' && <Topics accountType="persona" />}

      {tab === 'content' && (
        <div>
          <div className="mb-3 flex gap-1.5">
            {([
              ['scripts', '脚本工场'],
              ['digital', '口播数字人'],
              ['articles', '公众号长文'],
            ] as [typeof sub, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setSub(k)}
                className={`rounded-full px-3.5 py-1.5 text-xs ${sub === k ? 'bg-sky-100 text-sky-700' : 'border border-[#e8ecf0] bg-white text-slate-500'}`}>
                {label}
              </button>
            ))}
          </div>
          {sub === 'scripts' && <Scripts onNavigate={onNavigate} />}
          {sub === 'digital' && <Digital />}
          {sub === 'articles' && <Articles />}
        </div>
      )}

      {tab === 'style' && <Style onNavigate={onNavigate} />}
    </div>
  )
}
