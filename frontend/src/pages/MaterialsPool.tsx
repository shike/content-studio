import { useCallback, useEffect, useState } from 'react'
import { Check, Inbox, Link2, Package, RefreshCw, Trash2, Upload, X } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, relTime } from '../components'
import { Chips, KindBadge, MaterialDetail, Toolbar, type MaterialRow } from './MaterialsShared'

/**
 * 素材池（2026-11-18 用户定版：从「今天 · 推荐选题」退回素材库，做单独 tab）：
 * 全部素材的价值视图——待定夺收件箱 / 可用（按热度排）/ 已忽略翻案 / 全部检索，
 * 附贴链在线拆解与本地视频上传。每日取料的两条产线（热门评论/知识库）在「今天」页。
 */

type TopicChip = 'inbox' | 'ready' | 'ignored' | 'all'

const TOPIC_CHIPS: { v: TopicChip; label: string }[] = [
  { v: 'inbox', label: '待定夺' }, { v: 'ready', label: '可用' },
  { v: 'ignored', label: '已忽略' }, { v: 'all', label: '全部' },
]

export default function MaterialsPool() {
  const [chip, setChip] = useState<TopicChip>('ready')
  const [rows, setRows] = useState<MaterialRow[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [q, setQ] = useState('')
  const [author, setAuthor] = useState('')
  const [pasteUrl, setPasteUrl] = useState('')
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: '100' })
    if (chip !== 'all') params.set('status', chip)
    if (q.trim()) params.set('q', q.trim())
    if (author.trim()) params.set('author', author.trim())
    api<{ items: MaterialRow[]; total: number; counts: Record<string, number> }>(`/materials?${params}`)
      .then((d) => {
        const items = d.items || []
        // 可用视图按热度排（推荐语义：拆过的按分，没拆的靠后）
        if (chip === 'ready') {
          items.sort((a, b) => (b.quality_score ?? -1) - (a.quality_score ?? -1))
        }
        setRows(items); setTotal(d.total); setCounts(d.counts || {}); setLoaded(true)
      })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [chip, q, author])
  useEffect(() => { load() }, [load])

  const act = async (m: MaterialRow, action: 'ready' | 'ignored' | 'enrich' | 'delete') => {
    setError('')
    try {
      if (action === 'delete') {
        await api(`/materials/${m.id}`, { method: 'DELETE' })
        setMsg(`已删除 #${m.id}`)
      } else if (action === 'enrich') {
        const r = await api<{ job_id: number }>(`/materials/${m.id}/enrich`, { method: 'POST' })
        setMsg(`拆解任务 #${r.job_id} 已排队`)
      } else {
        await api(`/materials/${m.id}/status`, { method: 'POST', body: JSON.stringify({ status: action }) })
        setMsg(action === 'ready' ? '已收下' + (m.origin_type === 'douyin_video' ? '，拆解已排队' : '') : '已忽略')
      }
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const batch = async (action: 'approve' | 'ignore') => {
    try {
      const r = await api<{ resolved: number }>('/materials/inbox-resolve', {
        method: 'POST', body: JSON.stringify({ action }),
      })
      setMsg(action === 'approve' ? `批量收下 ${r.resolved} 条，已排队拆解` : `批量忽略 ${r.resolved} 条`)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const analyzeUrl = async () => {
    setError('')
    try {
      const r = await api<{ job_id: number }>('/materials/analyze-url', {
        method: 'POST', body: JSON.stringify({ url: pasteUrl }),
      })
      setPasteUrl('')
      setMsg(`在线拆解任务 #${r.job_id} 已排队`)
      waitJob(r.job_id, (j) => setMsg(`在线拆解：${j.progress}% ${j.message || ''}`)).then(() => load()).catch(() => load())
    } catch (e) { setError(`贴链拆解失败：${apiErrText(e)}`) }
  }

  const stLabel = (m: MaterialRow) =>
    m.status === 'ready' ? '可用' : m.status === 'inbox' ? '待定夺' : m.status === 'ignored' ? '已忽略' : '归档'

  return (
    <div>
      <Toolbar
        left={<>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜标题 / 作者 / 转写…"
            className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="按作者筛"
            className="w-32 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        </>}
        actions={<>
          <input value={pasteUrl} onChange={(e) => setPasteUrl(e.target.value)} placeholder="贴抖音链接/口令，在线拆解"
            className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <button onClick={analyzeUrl} disabled={!pasteUrl.trim()}
            className="rounded-full bg-[#0a2f24] px-3 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90 disabled:opacity-40">
            <Link2 size={13} className="inline" /> 拆这条
          </button>
          <label className="cursor-pointer rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
            <Upload size={13} className="inline" /> 上传拆解
            <input type="file" accept="video/*,audio/*" className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0]
                if (!f) return
                const fd = new FormData()
                fd.append('file', f)
                setError('')
                try {
                  const r = await api<{ job_id: number }>('/materials/upload', { method: 'POST', body: fd })
                  setMsg(`上传完成，拆解任务 #${r.job_id} 已排队`)
                } catch (err) { setError(apiErrText(err)) }
              }} />
          </label>
        </>} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Chips items={TOPIC_CHIPS.map((c) => ({
          v: c.v, label: c.label + (counts[c.v] != null && c.v !== 'ready' ? ` ${counts[c.v]}` : ''),
        }))} value={chip} onPick={(v) => setChip(v as TopicChip)} />
        <span className="text-[12px] text-slate-400">共 {total} 条{chip === 'ready' ? '（按热度排）' : ''}</span>
      </div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />
      {!loaded ? null : rows.length === 0 ? (
        chip === 'inbox' ? (
          <EmptyState icon={Inbox} title="没有待定夺的素材"
            desc="同行扫描/话题雷达的新视频先落这里；过质量闸（评论≥20或赞≥300）的自动入「可用」，不过闸的自动忽略——只有拿不准的才等你定夺" />
        ) : (
          <EmptyState icon={Package} title="没有匹配的素材" desc="换个筛选条件，或去「监控清单」扫一轮" />
        )
      ) : (
        <div className="space-y-2">
          {rows.map((m) => {
            const open = expanded === m.id
            return (
              <div key={m.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => setExpanded(open ? null : m.id)}>
                <div className="flex flex-wrap items-center gap-2">
                  {chip === 'inbox' && <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">新</span>}
                  <KindBadge m={m} />
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${m.status === 'ready' ? 'bg-emerald-50 text-emerald-700' : m.status === 'inbox' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500'}`}>
                    {stLabel(m)}{m.status === 'ready' && !m.analyzed ? '·待拆' : ''}{m.analyzed ? '·已拆' : ''}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">
                    {m.title || `素材 #${m.id}`}
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">@{m.author || '?'}</span>
                  {chip === 'inbox' ? (
                    <span className="shrink-0 text-[11px] text-slate-400">
                      评 {m.stats?.comment ?? '—'} · 赞 {m.stats?.digg ?? '—'} · {relTime(m.created_at || '')}
                    </span>
                  ) : (
                    <span className="shrink-0 text-[11px] text-slate-400">热度 {m.quality_score ?? '—'} · {relTime(m.created_at || '')}</span>
                  )}
                  {chip === 'inbox' && (
                    <>
                      <button onClick={(e) => { e.stopPropagation(); act(m, 'ready') }} title="收下并排队拆解"
                        className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                        <Check size={13} className="inline" /> 收下
                      </button>
                      <button onClick={(e) => { e.stopPropagation(); act(m, 'ignored') }} title="忽略"
                        className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-500 transition hover:bg-slate-50">
                        <X size={13} className="inline" /> 忽略
                      </button>
                    </>
                  )}
                  {chip !== 'inbox' && m.status !== 'ready' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'ready') }}
                      className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                      收下
                    </button>
                  )}
                  {chip !== 'inbox' && m.origin_type === 'douyin_video' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'enrich') }} title="强制（重）拆解"
                      className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                      <RefreshCw size={13} className="inline" /> 拆解
                    </button>
                  )}
                  {chip !== 'inbox' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'delete') }} title="删除素材"
                      className="shrink-0 rounded-md border border-red-100 px-2 py-1 text-[12px] text-red-500 transition hover:bg-red-50">
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
                {open && <MaterialDetail m={m} />}
              </div>
            )
          })}
          {chip === 'inbox' && rows.length > 0 && (
            <div className="flex items-center justify-end gap-2 pt-1">
              <button onClick={() => batch('approve')}
                className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                全部收下
              </button>
              <button onClick={() => batch('ignore')}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                全部忽略
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
