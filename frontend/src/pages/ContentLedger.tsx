import { useCallback, useEffect, useState } from 'react'
import { FileText, Search } from 'lucide-react'
import { api, apiErrText } from '../api'
import { EmptyState, ErrorLine } from '../components'

/**
 * 数据 · 稿件台账（R4-1 2026-11-22）：跨线全量稿件一屏总账——
 * 渠道/类型过滤 + 标题搜索，行内跳对应产线页编辑。
 */

interface LedgerRow {
  id: number
  kind: string
  title: string
  status: string
  channel: string
  created_at: string
}

const KIND_LABEL: Record<string, string> = {
  video_script: '口播', long_article: '长文', note: '笔记',
}
const CH_LABEL: Record<string, string> = {
  persona: '人设', leadgen: '获客', wechat: '公众号', xhs: '小红书',
}
const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  drafting: { label: '生成中', cls: 'bg-sky-100 text-sky-700' },
  review: { label: '待定稿', cls: 'bg-amber-100 text-amber-800' },
  ready: { label: '待发', cls: 'bg-emerald-100 text-emerald-700' },
  published: { label: '已发布', cls: 'bg-slate-200 text-slate-600' },
  failed: { label: '失败', cls: 'bg-red-100 text-red-700' },
  archived: { label: '归档', cls: 'bg-slate-100 text-slate-500' },
}

/** 行跳转：按 kind 回对应产线页（页面内自行定位到该稿）。 */
const NAV_BY_KIND: Record<string, string> = {
  video_script: 'persona', long_article: 'article', note: 'xhs',
}

export default function ContentLedger({ onNavigate }: { onNavigate?: (k: string) => void }) {
  const [rows, setRows] = useState<LedgerRow[]>([])
  const [total, setTotal] = useState(0)
  const [channel, setChannel] = useState('')
  const [kind, setKind] = useState('')
  const [q, setQ] = useState('')
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: '200' })
    if (channel) params.set('channel', channel)
    if (kind) params.set('kind', kind)
    if (q.trim()) params.set('q', q.trim())
    api<{ items: LedgerRow[]; total: number }>(`/contents?${params.toString()}`)
      .then((d) => { setRows(d.items || []); setTotal(d.total || 0); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [channel, kind, q])
  useEffect(load, [load])

  const chips = (cur: string, setter: (v: string) => void, opts: { key: string; label: string }[]) => (
    <div className="flex flex-wrap items-center gap-1.5">
      {opts.map((o) => (
        <button key={o.key} onClick={() => setter(o.key)}
          className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${cur === o.key ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
          {o.label}
        </button>
      ))}
    </div>
  )

  return (
    <div>
      <ErrorLine text={error} />
      <div className="mb-3 flex flex-wrap items-center gap-3">
        {chips(channel, setChannel, [
          { key: '', label: '全部渠道' },
          { key: 'persona', label: '人设' },
          { key: 'leadgen', label: '获客' },
          { key: 'wechat', label: '公众号' },
          { key: 'xhs', label: '小红书' },
        ])}
        {chips(kind, setKind, [
          { key: '', label: '全部类型' },
          { key: 'video_script', label: '口播' },
          { key: 'long_article', label: '长文' },
          { key: 'note', label: '笔记' },
        ])}
        <div className="relative ml-auto">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-300" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜标题…"
            className="w-52 rounded-lg border border-[#E8ECF0] pl-7 pr-2.5 py-1.5 text-[12.5px] outline-none focus:border-emerald-400" />
        </div>
      </div>

      <div className="mb-2 text-[12px] text-slate-400">共 {total} 篇{total > 200 ? '（显示最新 200）' : ''}</div>
      {!loaded ? null : rows.length === 0 ? (
        <EmptyState icon={FileText} title="没有匹配的稿件" desc="换个过滤条件，或清空搜索看看全量。" />
      ) : (
        <div className="card divide-y divide-slate-100 overflow-hidden p-0">
          {rows.map((r) => {
            const st = STATUS_LABEL[r.status] || { label: r.status, cls: 'bg-slate-100 text-slate-500' }
            return (
              <button key={r.id} onClick={() => onNavigate?.(NAV_BY_KIND[r.kind] || '')}
                className="flex w-full flex-wrap items-center gap-2 px-4 py-2.5 text-left transition hover:bg-slate-50">
                <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">{KIND_LABEL[r.kind] || r.kind}</span>
                <span className="shrink-0 rounded-full bg-slate-50 px-2 py-0.5 text-[11px] text-slate-400">{CH_LABEL[r.channel] || r.channel}</span>
                <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-slate-900">{r.title || `#${r.id}`}</span>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${st.cls}`}>{st.label}</span>
                <span className="shrink-0 text-[11px] text-slate-400">{(r.created_at || '').slice(0, 10)}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
