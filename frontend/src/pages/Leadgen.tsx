import { useState } from 'react'
import type { SectionKey } from '../App'
import Topics from './Topics'
import Scripts from './Scripts'

type Tab = 'topics' | 'content'

/** 获客号产线（2026-11-02 三线定版）：选题 → 脚本工场。打法卡库已移情报中心拆解资料库。 */
export default function Leadgen({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [tab, setTab] = useState<Tab>('topics')
  const [sub, setSub] = useState<'scripts'>('scripts')

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">获客号 · 内容产线</h2>
        <span className="text-xs text-slate-400">获客内容 · 引导设计已内置 · 表述合规受控</span>
      </div>
      <div className="mb-3 flex gap-1.5">
        {([
          ['topics', '选题'],
          ['content', '脚本工场'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'topics' && <Topics accountType="leadgen" />}

      {tab === 'content' && <Scripts onNavigate={onNavigate} accountType="leadgen" />}
    </div>
  )
}
