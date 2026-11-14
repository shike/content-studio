import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, ListChecks, Loader2, Pickaxe } from 'lucide-react'
import { api, apiErrText, ContentItem } from '../api'
import { ConfirmDeleteButton, EmptyState, ErrorLine, PageHeader, relTime } from '../components'
import { usePageGuard } from '../hooks'

/** 内容清单（2026-11-11 源文改编产线）：知识库逐篇评估出的高质量源文。
 *  改编器每日自动取项成文——这里看清单、调优先级、标不做，不插手也照转。
 *  筛选两行分开：上=状态（含计数），下=栏目（含计数）；「全部」默认不显示已标不做。 */

const I_BADGE: Record<string, { label: string; cls: string }> = {
  pending: { label: '待改编', cls: 'bg-amber-100 text-amber-800' },
  running: { label: '改编中', cls: 'bg-sky-100 text-sky-700' },
  done: { label: '已改编', cls: 'bg-emerald-100 text-emerald-700' },
  published: { label: '已发布', cls: 'bg-slate-800 text-slate-100' },
  low: { label: '低分未入', cls: 'bg-slate-100 text-slate-500' },
  dismissed: { label: '不做', cls: 'bg-slate-100 text-slate-400' },
}
const STATUS_FLOW: [string, string][] = [
  ['pending', '待改编'], ['running', '改编中'], ['done', '已改编'],
  ['published', '已发布'], ['low', '低分未入'], ['dismissed', '不做'],
]
const CATS = ['转行真话', '交付现场', '行业拆局', '变现报价', '现场实录']
const STATUS_ORDER: Record<string, number> = {
  running: 0, pending: 1, done: 2, published: 3, low: 4, dismissed: 5,
}

interface LedgerRow {
  id: number
  phase: string
  produced: string
  reason: string
  anomaly?: boolean
}

export default function ContentItems({ onNavigate }: { onNavigate: (k: string) => void }) {
  const [items, setItems] = useState<ContentItem[]>([])
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [catCounts, setCatCounts] = useState<Record<string, number>>({})
  const [status, setStatus] = useState('')
  const [cat, setCat] = useState('')
  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  const [booted, setBooted] = useState(false)
  const [mining, setMining] = useState(false)
  const { guard, busy, error, setError } = usePageGuard()

  const load = useCallback(async () => {
    try {
      const d = await api<{
        items: ContentItem[]
        total: number
        status_counts: Record<string, number>
        category_counts: Record<string, number>
      }>('/articles/content-items?limit=500')
      let list = d.items || []
      if (status) list = list.filter((x) => x.status === status)
      else list = list.filter((x) => x.status !== 'dismissed') // 「全部」不显示已标不做
      if (cat) list = list.filter((x) => x.category === cat)
      list.sort((a, b) =>
        (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
        || b.priority - a.priority || b.quality_score - a.quality_score || b.id - a.id)
      setItems(list)
      setStatusCounts(d.status_counts || {})
      setCatCounts(d.category_counts || {})
      const l = await api<{ items: LedgerRow[] }>('/articles/ledger?limit=10')
      setLedger(l.items || [])
      setError('')
    } catch (e) {
      setError(apiErrText(e))
    } finally {
      setBooted(true)
    }
  }, [status, cat, setError])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!items.some((x) => x.status === 'running')) return
    const t = setInterval(() => { load() }, 60_000)
    return () => clearInterval(t)
  }, [items, load])

  const act = async (it: ContentItem, action: 'pin' | 'dismiss' | 'pending') => {
    await guard(async () => {
      if (action === 'pin') await api(`/articles/content-items/${it.id}/pin`, { method: 'POST' })
      else await api(`/articles/content-items/${it.id}/status`,
        { method: 'POST', body: JSON.stringify({ status: action }) })
      await load()
    })
  }
  const mine = async () => {
    setMining(true)
    try {
      await api('/articles/mine', { method: 'POST' })
      setTimeout(() => { setMining(false); load() }, 1500)
    } catch (e) {
      setError(apiErrText(e)); setMining(false)
    }
  }

  const mineRow = ledger.find((x) => x.phase === 'mine')
  const adaptRow = ledger.find((x) => x.phase === 'adapt')
  const phaseLine = (r: LedgerRow | undefined, name: string) => {
    if (!r) return { text: `${name}：还没跑过`, anomaly: false }
    if (r.anomaly) return { text: `${name}：成功但无产出且无原因——异常，去任务页查`, anomaly: true }
    return { text: `${name}：${r.produced || r.reason || '空过'}`, anomaly: false }
  }
  const mineLine = phaseLine(mineRow, '昨次挖掘')
  const adaptLine = phaseLine(adaptRow, '昨次改编')
  const pendingN = statusCounts.pending || 0
  const totalN = Object.values(statusCounts).reduce((a, b) => a + b, 0)

  if (!booted) return <div className="text-sm text-slate-400">载入内容清单…</div>

  return (
    <div>
      <PageHeader icon={ListChecks} title="内容清单"
        desc="知识库里逐篇评出的高质量源文——每天自动改编一篇成我们号的文章：保源文骨架与最佳原话，换我们的判断口径和读者钩子。低分源文不进队列。" />
      {error && <div className="mt-4"><ErrorLine text={error} /></div>}

      <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[13px] text-slate-500">
        <span className={mineLine.anomaly || adaptLine.anomaly ? 'font-medium text-red-600' : ''}>
          {mineLine.text}
        </span>
        <span className="text-slate-300">·</span>
        <span className={adaptLine.anomaly ? 'font-medium text-red-600' : ''}>{adaptLine.text}</span>
        <button className="btn-ghost btn-sm ml-auto flex items-center gap-1" disabled={mining || !!busy} onClick={mine}>
          {mining ? <Loader2 size={13} className="animate-spin" /> : <Pickaxe size={13} />} 手动补挖
        </button>
      </div>

      {/* 状态筛选行 */}
      <div className="mt-4 flex flex-wrap items-center gap-2 text-[13px]">
        <button className={`rounded-full px-3 py-1 font-medium ${status === '' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
          onClick={() => setStatus('')}>
          全部 <span className="num-serif">{totalN}</span>
        </button>
        {STATUS_FLOW.map(([k, label]) => (
          <button key={k} className={`rounded-full px-3 py-1 ${status === k ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
            onClick={() => setStatus(status === k ? '' : k)}>
            {label} <span className="num-serif">{statusCounts[k] || 0}</span>
          </button>
        ))}
      </div>
      {/* 栏目筛选行 */}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
        <span className="text-[11px] text-slate-400">栏目</span>
        {CATS.map((c) => (
          <button key={c} className={`rounded-full border px-2.5 py-0.5 ${cat === c ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 text-slate-500 hover:border-slate-400'}`}
            onClick={() => setCat(cat === c ? '' : c)}>
            {c} <span className="num-serif">{catCounts[c] || 0}</span>
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <div className="mt-8"><EmptyState icon={ListChecks} title={status === 'pending' && pendingN === 0 ? '待改编清空了' : '没有符合条件的源文'}
          desc="挖掘器每天 04:00 评一批知识库源文，达线（≥7.5 分）自动入清单。不想等就点右上角「手动补挖」。" /></div>
      ) : (
        <div className="mt-4 space-y-2">
          {items.map((it) => {
            const badge = I_BADGE[it.status] || { label: it.status, cls: 'bg-slate-100 text-slate-600' }
            const open = expanded === it.id
            const dim = it.status === 'low'
            return (
              <div key={it.id} className={`rounded-2xl border bg-white transition ${open ? 'border-emerald-300 shadow-sm' : 'border-[#E8ECF0]'} ${dim ? 'opacity-55' : ''}`}>
                <div className="flex cursor-pointer items-center gap-3 px-4 py-3" onClick={() => setExpanded(open ? null : it.id)}>
                  {open ? <ChevronDown size={15} className="shrink-0 text-slate-400" /> : <ChevronRight size={15} className="shrink-0 text-slate-400" />}
                  <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.cls}`}>
                    {it.status === 'running' && <Loader2 size={11} className="animate-spin" />}{badge.label}
                  </span>
                  {it.priority > 0 && it.status === 'pending' && (
                    <span className="shrink-0 rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700">置顶</span>
                  )}
                  {it.category && (
                    <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">{it.category}</span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-slate-800">
                    《{it.title || '(无题)'}》
                  </span>
                  <span className="hidden shrink-0 items-center gap-2 text-xs text-slate-400 sm:flex">
                    <span>质量 <span className="num-serif font-medium text-slate-600">{it.quality_score.toFixed(1)}</span></span>
                    {it.source_author && <span className="max-w-[110px] truncate">{it.source_author}</span>}
                    <span>{relTime(it.created_at || '')}</span>
                  </span>
                </div>
                {open && (
                  <div className="border-t border-[#E8ECF0] px-4 py-4">
                    <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
                      <div className="min-w-0 space-y-2.5 text-[13px] leading-relaxed text-slate-700">
                        {it.why_good && (
                          <div><span className="text-[11px] tracking-wide text-slate-400">为什么值得发</span><div>{it.why_good}</div></div>
                        )}
                        {it.adapt_angle && (
                          <div className="rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2 text-[12.5px] text-blue-900">
                            <b>改编角度</b>：{it.adapt_angle}
                          </div>
                        )}
                        {!!it.best_quotes.length && (
                          <div>
                            <span className="text-[11px] tracking-wide text-slate-400">最佳原话（改编时保留引用）</span>
                            <div className="mt-1 space-y-1.5">
                              {it.best_quotes.map((q, i) => (
                                <div key={i} className="border-l-2 border-[#C9A25C] bg-[#FBFAF5] px-3 py-1.5 text-[12.5px] text-[#4A4A3F]">{q}</div>
                              ))}
                            </div>
                          </div>
                        )}
                        {it.note && <div className="text-xs text-slate-400">{it.note}</div>}
                      </div>
                      <div className="space-y-3 text-[13px] text-slate-600">
                        <div>
                          <span className="text-[11px] text-slate-400">来源</span>
                          <div className="mt-0.5"><span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{it.folder_name || '知识库'}</span></div>
                        </div>
                        {it.article_id && (
                          <div>
                            <span className="text-[11px] text-slate-400">去向</span>
                            <div>文章 <span className="num-serif">#{it.article_id}</span>
                              <button className="ml-1 underline decoration-slate-300 underline-offset-2 hover:text-slate-900"
                                onClick={(e) => { e.stopPropagation(); onNavigate('article') }}>去内容工场看 →</button>
                            </div>
                          </div>
                        )}
                        <div className="flex flex-wrap gap-2">
                          {it.status === 'pending' && it.priority === 0 && (
                            <button className="btn-accent btn-sm" disabled={!!busy} onClick={(e) => { e.stopPropagation(); act(it, 'pin') }}>
                              明天改编
                            </button>
                          )}
                          {it.status === 'pending' && (
                            <ConfirmDeleteButton onConfirm={() => act(it, 'dismiss')} confirmText="不做" />
                          )}
                          {(it.status === 'low' || it.status === 'dismissed') && (
                            <button className="btn-ghost btn-sm" disabled={!!busy} onClick={(e) => { e.stopPropagation(); act(it, 'pending') }}>
                              {it.status === 'low' ? '强制入清单' : '恢复待改编'}
                            </button>
                          )}
                        </div>
                        <div className="text-[11px] leading-relaxed text-slate-400">
                          改编规则（机检强制）：源文骨架可用 · 逐字引用整体重复率 ≤30% · 判断口径与钩子必须是我们的 · 事实审校不过不进待发。
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
