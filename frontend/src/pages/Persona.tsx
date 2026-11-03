import { useState } from 'react'
import type { SectionKey } from '../App'
import Topics from './Topics'
import Scripts from './Scripts'
import Style from './Style'
import TopicInsights from './TopicInsights'
import AccountTrends from './AccountTrends'
import LineIntel from './LineIntel'

type Tab = 'topics' | 'content' | 'style' | 'own' | 'intel'

/** 人设号产线（2026-11-03 IA 定版：情报并入产线，本线全部住处——
选题（含话题雷达）→ 内容看板（脚本工场）→ 风格画像 → 账号经营（自有数据+周报）→ 对标情报（拆解库/候选/监控）。长文已独立成公众号线。 */
export default function Persona({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [tab, setTab] = useState<Tab>('topics')

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">人设号</h2>
        <span className="text-xs text-slate-400">高质量内容 · 不带获客引导 · 自动套用你的风格画像</span>
      </div>
      <div className="mb-3 flex gap-1.5">
        {([
          ['topics', '选题'],
          ['content', '内容看板'],
          ['style', '风格画像'],
          ['own', '账号经营'],
          ['intel', '对标情报'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'topics' && (
        <div>
          <Topics accountType="persona" />
          {/* 话题雷达（2026-11-03 并入本 tab）：监测话题+飙升检查，是选题的进料口 */}
          <div className="mt-6"><TopicInsights /></div>
        </div>
      )}

      {tab === 'content' && <Scripts onNavigate={onNavigate} accountType="persona" />}

      {tab === 'style' && <Style onNavigate={onNavigate} />}

      {tab === 'own' && <AccountTrends />}

      {tab === 'intel' && <LineIntel line="persona" />}
    </div>
  )
}
