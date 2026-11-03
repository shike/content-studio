import { useState } from 'react'
import type { SectionKey } from '../App'
import Topics from './Topics'
import Scripts from './Scripts'
import LineIntel from './LineIntel'

type Tab = 'topics' | 'content' | 'intel'

/** 获客号产线（2026-11-03 IA 定版：情报并入产线）——选题 → 脚本工场 →
挖号情报（纯获客候选/获客监控/打法卡库，LLM 终审口径判定纯号）。 */
export default function Leadgen({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [tab, setTab] = useState<Tab>('topics')

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">获客号</h2>
        <span className="text-xs text-slate-400">获客内容 · 引导设计已内置 · 表述合规受控</span>
      </div>
      <div className="mb-3 flex gap-1.5">
        {([
          ['topics', '选题'],
          ['content', '脚本工场'],
          ['intel', '挖号情报'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'topics' && <Topics accountType="leadgen" />}

      {tab === 'content' && <Scripts onNavigate={onNavigate} accountType="leadgen" />}

      {tab === 'intel' && <LineIntel line="leadgen" />}
    </div>
  )
}
