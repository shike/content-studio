import { useState } from 'react'
import Benchmarks from './Benchmarks'
import DiscoveredAccounts from './DiscoveredAccounts'
import Watch from './Watch'

type Line = 'persona' | 'leadgen'

/** 分线情报区（2026-11-03 IA 定版：情报中心解散并入产线）——每条产线的「情报」标签内容。
人设号=对标情报（拆解资料库/候选定夺/监控清单）；获客号=挖号情报（纯获客候选/获客监控/打法卡库）。
顶部贴链框已砍（2026-11-07 用户逐组拍板 ⑫）：情报获取只走搜索矩阵挖掘与监控链自动化，不再手工贴链。 */
export default function LineIntel({ line }: { line: Line }) {
  const tabs: [string, string][] = line === 'persona'
    ? [['bench', '拆解资料库'], ['cand', '候选定夺'], ['mon', '监控清单']]
    : [['cand', '纯获客候选'], ['mon', '获客监控'], ['bench', '打法卡库']]
  const [tab, setTab] = useState(tabs[0][0])

  return (
    <div>
      <div className="mb-3 flex gap-1.5">
        {tabs.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'bench' && <Benchmarks accountType={line} />}
      {tab === 'cand' && <DiscoveredAccounts accountType={line} />}
      {tab === 'mon' && <Watch accountType={line} />}
    </div>
  )
}
