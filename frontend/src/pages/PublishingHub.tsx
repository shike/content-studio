import { useState } from 'react'
import Publishing from './Publishing'
import PosterPrompts from './PosterPrompts'

type Tab = 'publish' | 'posters'

/** 发布台枢纽：物料包 + 人工发布清单 + 海报提示词（并入发布域）。 */
export default function PublishingHub() {
  const [tab, setTab] = useState<Tab>('publish')

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">发布台</h2>
        <span className="text-xs text-slate-400">发布永远人工 · 物料包一键复制 · 海报提示词并入</span>
      </div>
      <div className="mb-3 flex gap-1.5">
        {([
          ['publish', '物料包与发布'],
          ['posters', '海报提示词'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'publish' && <Publishing />}
      {tab === 'posters' && <PosterPrompts />}
    </div>
  )
}
