import { useEffect, useState } from 'react'
import type { SectionKey } from '../App'
import { api } from '../api'
import Benchmarks from './Benchmarks'
import Watch from './Watch'
import AccountTrends from './AccountTrends'

type Tab = 'bench' | 'accounts' | 'trend'

/** 情报中心：拆解资料库 / 监测账号 / 账号经营 三区（同行动态已并入拆解资料库，话题雷达已并入「热点雷达」，风格画像在人设号产线）。 */
export default function Intel({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [tab, setTab] = useState<Tab>('bench')
  const [error, setError] = useState('')

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">情报中心</h2>
        <span className="text-xs text-slate-400">扫描与拆解凌晨自动运转 · 探针 2 小时自检</span>
      </div>
      <div className="mb-3 flex gap-1.5">
        {([
          ['stream', '同行动态'],
          ['bench', '拆解资料库'],
          ['accounts', '监测账号'],
          ['trend', '我的账号趋势'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {error && <div className="mb-2 text-sm text-red-600">{error}</div>}

      {tab === 'bench' && <Benchmarks onNavigate={onNavigate} />}
      {tab === 'accounts' && <Watch onNavigate={onNavigate} />}
      {tab === 'trend' && <AccountTrends />}
    </div>
  )
}
