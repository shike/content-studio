import { useCallback, useEffect, useState } from 'react'
import { CalendarDays } from 'lucide-react'
import { api, apiErrText } from '../api'
import { EmptyState, ErrorLine } from '../components'

/**
 * 数据 · 排期（R3-2 2026-11-22）：发布排期日历——人工决定哪天发（定稿→排期→发布登记）。
 * 排期=Content.payload.publish_plan；发布登记后作废；排期入口集中在本页，三线列表页不动。
 */

interface SchedItem { content_id: number; title: string; kind: string; platform: string; publish_plan: string }
interface UnschedItem { id: number; title: string; kind: string; platform: string }
interface PubItem { content_id: number; platform: string; published_at: string; title: string }
interface ScheduleData {
  upcoming: { date: string; items: SchedItem[] }[]
  unscheduled: UnschedItem[]
  published_week: PubItem[]
  today: string
}

const KIND_LABEL: Record<string, string> = {
  video_script: '口播', long_article: '公众号', note: '小红书',
}
const PL_LABEL: Record<string, string> = { douyin: '抖音', wechat: '公众号', xiaohongshu: '小红书' }
const PL_BADGE: Record<string, string> = {
  douyin: 'bg-slate-900 text-white', wechat: 'bg-emerald-100 text-emerald-700',
  xiaohongshu: 'bg-rose-100 text-rose-600',
}

export default function ScheduleTab() {
  const [data, setData] = useState<ScheduleData | null>(null)
  const [error, setError] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [editVal, setEditVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api<ScheduleData>('/analytics/schedule?days=14')
      .then(setData)
      .catch((e) => setError(apiErrText(e)))
  }, [])
  useEffect(load, [load])

  const savePlan = async (id: number, val: string) => {
    setError(''); setMsg(''); setBusy(true)
    try {
      await api(`/contents/${id}/schedule`, { method: 'PUT', body: JSON.stringify({ publish_at: val }) })
      setMsg(val ? '已排期' : '已清除')
      setEditId(null)
      load()
    } catch (e) { setError(apiErrText(e)) } finally { setBusy(false) }
  }

  if (!data) return <div><ErrorLine text={error} /><div className="py-6 text-center text-sm text-slate-300">排期加载中…</div></div>

  const dayCells = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(data.today + 'T00:00:00')
    d.setDate(d.getDate() + i)
    const key = d.toISOString().slice(0, 10)
    return { key, label: `${d.getMonth() + 1}/${d.getDate()}`, isToday: i === 0, items: (data.upcoming.find((u) => u.date === key)?.items) || [] }
  })

  return (
    <div>
      <ErrorLine text={error} />
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12.5px] text-emerald-700">{msg}</div>}

      {/* 未来两周日历 */}
      <div className="card mb-4 p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-800">
          <CalendarDays size={15} className="text-emerald-600" /> 未来两周排期
          <span className="text-xs font-normal text-slate-400">排期=人工定的发布日；发布登记后自动出排期</span>
        </div>
        <div className="grid grid-cols-7 gap-1.5">
          {dayCells.map((c) => (
            <div key={c.key}
              className={`min-h-[88px] rounded-lg border px-2 py-1.5 ${c.isToday ? 'border-emerald-300 bg-emerald-50/50' : 'border-slate-100 bg-slate-50/40'}`}>
              <div className={`text-[11px] font-medium ${c.isToday ? 'text-emerald-700' : 'text-slate-400'}`}>
                {c.label}{c.isToday ? ' · 今天' : ''}
              </div>
              <div className="mt-1 space-y-1">
                {c.items.map((it) => (
                  <div key={it.content_id} className="rounded bg-white px-1.5 py-1 text-[11px] leading-snug shadow-sm">
                    <span className={`mr-1 inline-block rounded px-1 py-px text-[10px] ${PL_BADGE[it.platform] || 'bg-slate-100'}`}>
                      {PL_LABEL[it.platform] || it.platform}
                    </span>
                    <span className="text-slate-700">{it.title.slice(0, 22) || `#${it.content_id}`}</span>
                    <button onClick={() => { setEditId(it.content_id); setEditVal(it.publish_plan) }}
                      className="ml-1 text-[10px] text-sky-600 hover:underline">改</button>
                    <button onClick={() => savePlan(it.content_id, '')}
                      className="ml-0.5 text-[10px] text-slate-400 hover:text-red-500">撤</button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        {editId !== null && (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-sky-200 bg-sky-50/50 px-3 py-2">
            <span className="text-[12.5px] text-slate-600">排期 #{editId}：</span>
            <input value={editVal} onChange={(e) => setEditVal(e.target.value)} placeholder="YYYY-MM-DD HH:MM"
              className="w-44 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[12.5px] outline-none focus:border-emerald-400" />
            <button onClick={() => savePlan(editId, editVal.trim())} disabled={busy} className="btn-accent btn-xs">保存</button>
            <button onClick={() => savePlan(editId, '')} disabled={busy} className="btn-ghost btn-xs">清除排期</button>
            <button onClick={() => setEditId(null)} className="btn-ghost btn-xs">取消</button>
          </div>
        )}
      </div>

      {/* 待排期 */}
      <div className="card mb-4 p-4">
        <div className="mb-2 text-sm font-semibold text-slate-800">
          待排期 <span className="text-xs font-normal text-slate-400">定稿未登记未排期的稿——点「排期」定日子</span>
        </div>
        {data.unscheduled.length === 0 ? (
          <div className="text-[12.5px] text-slate-400">没有待排期的稿（全部已排期或已发布）</div>
        ) : (
          <div className="space-y-1.5">
            {data.unscheduled.map((x) => (
              <div key={x.id} className="flex items-center gap-2 text-[13px]">
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${PL_BADGE[x.platform] || 'bg-slate-100'}`}>
                  {PL_LABEL[x.platform] || x.platform}
                </span>
                <span className="min-w-0 flex-1 truncate text-slate-700">{x.title}</span>
                <button onClick={() => { setEditId(x.id); setEditVal('') }}
                  className="shrink-0 text-[12px] text-sky-600 underline decoration-sky-200 underline-offset-2 hover:text-sky-800">排期</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 本周已发布 */}
      <div className="card p-4">
        <div className="mb-2 text-sm font-semibold text-slate-800">本周已发布（实发，对照节奏目标）</div>
        {data.published_week.length === 0 ? (
          <EmptyState icon={CalendarDays} title="本周还没有发布登记"
            desc="发布后在对应产线页登记链接；排期和节奏目标在「设置 · 发布节奏」里调。" />
        ) : (
          <div className="space-y-1.5">
            {data.published_week.map((x) => (
              <div key={x.content_id} className="flex items-center gap-2 text-[13px]">
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${PL_BADGE[x.platform] || 'bg-slate-100'}`}>
                  {PL_LABEL[x.platform] || x.platform}
                </span>
                <span className="min-w-0 flex-1 truncate text-slate-700">{x.title}</span>
                <span className="shrink-0 text-[11px] text-slate-400">{x.published_at}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
