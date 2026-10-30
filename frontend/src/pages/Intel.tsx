import { useEffect, useState } from 'react'
import type { SectionKey } from '../App'
import { api } from '../api'
import Watch from './Watch'
import Benchmarks from './Benchmarks'
import Style from './Style'

type Tab = 'stream' | 'bench' | 'accounts' | 'trend'

interface BenchRow {
  id: number
  title: string
  url?: string
  author: string
  account_type?: string
  quality_score?: number
  scan_status?: string
  analysis?: Record<string, any>
  created_at?: string
}

/** 情报中心：同行监测/拆解库/获客雷达/我的风格 整合页（话题雷达已并入今日工作台「热点雷达」），自动运转只读流 + 全量管理入口。 */
export default function Intel({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [tab, setTab] = useState<Tab>('stream')
  const [rows, setRows] = useState<BenchRow[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (tab !== 'stream') return
    api<{ items: BenchRow[] }>('/benchmarks?limit=30')
      .then((d) => {
        const sorted = (d.items || []).slice().sort(
          (a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0))
        setRows(sorted)
      })
      .catch((e) => setError(String(e)))
  }, [tab])

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">情报中心</h2>
        <span className="text-xs text-slate-400">全自动运转 · 探针 15 分钟自检 · 无需巡视</span>
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

      {tab === 'stream' && (
        <div>
          <div className="mb-2.5 text-xs text-slate-500">
            自动拆解完成流（高热度置顶）——每条已按内容类型完成拆解，选题候选自动进选题库。
          </div>
          {rows.length === 0 ? (
            <div className="rounded-2xl border border-[#e8ecf0] bg-white p-8 text-center text-sm text-slate-400">
              暂无拆解记录——同行扫描与雷达命中会自动进入这里。
            </div>
          ) : (
            <div className="rounded-2xl border border-[#e8ecf0] bg-white px-4 py-2">
              {rows.map((b) => {
                const leadgen = b.account_type === 'leadgen'
                const dot = leadgen ? 'mu-dot-a' : b.scan_status === 'ignored' ? 'mu-dot-i' : 'mu-dot-g'
                return (
                  <div key={b.id} className="flex items-start gap-3 border-b border-dashed border-[#e8ecf0] py-2.5 last:border-0">
                    <span className={`mu-dot-s ${dot} mt-1.5 shrink-0`}></span>
                    <div className="min-w-0 flex-1">
                      <b className="block truncate text-[13px] font-medium">{b.title || b.url}</b>
                      <span className="text-[11px] text-slate-400">
                        {b.author || '未知作者'}
                        {leadgen ? ' · 获客打法拆解' : ' · 人设内容拆解'}
                        {b.scan_status === 'ignored' && ' · 已忽略'}
                        {b.created_at && ` · ${(b.created_at || '').slice(5, 16).replace('T', ' ')}`}
                      </span>
                    </div>
                    {(b.quality_score ?? 0) > 0 && (
                      <span className={`shrink-0 rounded-lg px-2 py-0.5 text-xs font-semibold tabular-nums ${(b.quality_score ?? 0) >= 6 ? 'bg-orange-100 text-orange-700' : 'bg-slate-100 text-slate-400'}`}>
                        热度 {Number(b.quality_score).toFixed(1)}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          )}
          <div className="mt-2 text-[11px] text-slate-400">
            自动化参数（设置页同源）：质量闸 开 · 热度分门槛 6 · 每日自动拆解上限 6 · 同行扫描 01:00 · 指标回流 06:00 · 今日热点 07:00。
          </div>
          <div className="mu-legend-note">
            <style>{`
              .mu-dot-s { display: inline-block; width: 7px; height: 7px; border-radius: 50%; }
              .mu-dot-g { background: #29c16a; }
              .mu-dot-a { background: #c9a25c; }
              .mu-dot-i { background: #d5dbe0; }
            `}</style>
          </div>
        </div>
      )}

      {tab === 'bench' && <Benchmarks onNavigate={onNavigate} />}
      {tab === 'accounts' && <Watch onNavigate={onNavigate} />}
      {tab === 'trend' && <Style onNavigate={onNavigate} />}
    </div>
  )
}
