import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

export interface PickItem { id: number; title: string; angle: string; sharpness: number }

/** 今日三锐（2026-11-05 ④）：锐度≥7 的 draft 选题按锐度降序取 3 条，换一批=顺位后移。
工作台与手机页共用；批准=定审 approved（进入对应产线）。 */
export default function SharpPicks({ line, label, compact, onApprove }:
  { line: 'persona' | 'leadgen'; label: string; compact?: boolean; onApprove?: (id: number) => void }) {
  const [items, setItems] = useState<PickItem[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')

  const reload = useCallback(async (o: number) => {
    try {
      const r = await api<{ total: number; items: PickItem[] }>(`/topics/picks?line=${line}&offset=${o}`)
      setItems(r.items)
      setTotal(r.total)
      setError('')
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    }
  }, [line])

  useEffect(() => { reload(offset).catch(() => {}) }, [reload, offset])

  const approve = async (id: number) => {
    await api(`/topics/${id}/approve`, { method: 'POST' })
    setItems((m) => m.filter((x) => x.id !== id))
    setTotal((t) => Math.max(0, t - 1))
    onApprove?.(id)
  }

  const pages = Math.max(1, Math.ceil(total / 3))

  return (
    <div className={compact ? '' : 'rounded-2xl border border-[#e8ecf0] bg-white p-4'}>
      <div className="mb-2 flex items-center gap-2">
        <b className={compact ? 'text-sm font-semibold' : 'text-sm font-semibold text-zinc-900'}>{label}</b>
        <span className="text-xs text-slate-400">锐度≥7 共 {total} 条 · 今天先做这 3 条</span>
        <button onClick={() => setOffset((o) => (o + 1) % pages)}
          className="ml-auto rounded-full border border-[#e8ecf0] bg-white px-3 py-1 text-xs text-slate-500 hover:bg-slate-50">
          换一批
        </button>
      </div>
      {error && <div className="text-xs text-red-600">{error}</div>}
      {!error && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-[#e3e8ed] p-4 text-center text-xs text-slate-400">
          池子里还没有锐度达标的选题——夜间评分器持续打分，或去产线快速记题
        </div>
      )}
      <div className="space-y-1.5">
        {items.map((it) => (
          <div key={it.id} className="flex items-center gap-3 rounded-xl border border-[#eef1f4] px-3.5 py-2.5">
            <span className="badge shrink-0 bg-emerald-100 text-emerald-700">锐 {it.sharpness}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium text-slate-800">{it.title}</div>
              {it.angle && <div className="truncate text-[11px] text-slate-400">{it.angle}</div>}
            </div>
            <button onClick={() => approve(it.id)}
              className="btn-accent btn-xs shrink-0">批准定审</button>
          </div>
        ))}
      </div>
    </div>
  )
}
