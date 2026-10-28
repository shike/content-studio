import { useState } from 'react'
import type { SectionKey } from '../App'
import Topics from './Topics'
import Scripts from './Scripts'
import Digital from './Digital'
import Benchmarks from './Benchmarks'

type Tab = 'topics' | 'content' | 'playbook'

/** 获客号产线：选题 → 内容看板（获客脚本/成片）→ 打法卡库。 */
export default function Leadgen({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [tab, setTab] = useState<Tab>('playbook')
  const [sub, setSub] = useState<'scripts' | 'digital'>('scripts')

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">获客号 · 内容产线</h2>
        <span className="text-xs text-slate-400">简单粗暴获客 · 引导话术内置 · 合规红线仍约束</span>
      </div>
      <div className="mb-3 flex gap-1.5">
        {([
          ['topics', '选题'],
          ['content', '内容看板'],
          ['playbook', '打法卡库'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'topics' && <Topics accountType="leadgen" />}

      {tab === 'content' && (
        <div>
          <div className="mb-3 flex gap-1.5">
            {([
              ['scripts', '获客脚本'],
              ['digital', '口播成片'],
            ] as [typeof sub, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setSub(k)}
                className={`rounded-full px-3.5 py-1.5 text-xs ${sub === k ? 'bg-amber-100 text-amber-800' : 'border border-[#e8ecf0] bg-white text-slate-500'}`}>
                {label}
              </button>
            ))}
          </div>
          {sub === 'scripts' && <Scripts onNavigate={onNavigate} />}
          {sub === 'digital' && <Digital />}
        </div>
      )}

      {tab === 'playbook' && <Benchmarks onNavigate={onNavigate} />}
    </div>
  )
}
