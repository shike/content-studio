import { useCallback, useEffect, useState } from 'react'
import { Lightbulb, Plus } from 'lucide-react'
import { api, apiErrText } from '../api'
import { EmptyState, ErrorLine, relTime } from '../components'

/**
 * 素材库 · 母题（M9 2026-11-22）：一个母题派生四线成品（口播/长文/小红书/朋友圈）。
 * 母题层管三件事：一键派生、追踪矩阵、跨线不重做。各成品独立走各线生命周期。
 */

interface LineState {
  n: number
  items: { id: number; title: string; status: string; content_id?: number | null; posts?: { body: string; visual: string; content_type: string }[] }[]
}
interface MotifRow {
  id: number
  title: string
  note: string
  status: string
  created_at: string
  counts?: Record<string, { n: number }>
  counts_full?: Record<string, LineState>
}
type Detail = MotifRow & { counts: Record<string, LineState> }

const LINES: { key: string; label: string }[] = [
  { key: 'script', label: '口播' },
  { key: 'article', label: '公众号' },
  { key: 'note', label: '小红书' },
  { key: 'moments', label: '朋友圈' },
]
const DOT_CLS = (n: number) => (n > 0 ? 'bg-emerald-500' : 'bg-slate-200')
const ST_LABEL: Record<string, string> = {
  draft: '选题已建', approved: '已备稿', consumed: '已成稿', discarded: '不做',
  drafting: '生成中', review: '待定稿/待发', ready: '待发', published: '已发布', failed: '失败',
}

export default function MotifsTab({ onNavigate }: { onNavigate?: (k: string) => void } = {}) {
  const [rows, setRows] = useState<MotifRow[]>([])
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailErr, setDetailErr] = useState('')
  // 新建表单
  const [title, setTitle] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api<{ items: MotifRow[] }>('/motifs')
      .then((d) => { setRows(d.items || []); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [])
  useEffect(load, [load])

  const create = async () => {
    setError(''); setBusy(true)
    try {
      await api('/motifs', { method: 'POST', body: JSON.stringify({ title: title.trim(), note: note.trim() }) })
      setTitle(''); setNote('')
      load()
    } catch (e) { setError(apiErrText(e)) } finally { setBusy(false) }
  }

  const openDetail = async (id: number) => {
    if (expanded === id) { setExpanded(null); setDetail(null); return }
    setExpanded(id); setDetail(null); setDetailErr('')
    try {
      const d = await api<Detail>(`/motifs/${id}`)
      setDetail(d)
    } catch (e) { setDetailErr(apiErrText(e)) }
  }

  const derive = async (id: number, kind: string) => {
    setError(''); setMsg(''); setDetailErr('')
    try {
      const d = await api<{ kind: string; next?: string; job_id?: number }>(`/motifs/${id}/derive`, {
        method: 'POST', body: JSON.stringify({ kind }),
      })
      if (d.next) { setMsg(d.next); setExpanded(null); load() } else {
        setMsg('派生任务已提交，稍后在本行看状态')
        const det = await api<Detail>(`/motifs/${id}`)
        setDetail(det)
      }
    } catch (e) { setDetailErr(apiErrText(e)) }
  }

  const markPublished = async (cid: number) => {
    setError(''); setMsg('')
    try {
      await api(`/motifs/moments/${cid}/published`, { method: 'POST' })
      setMsg('已标记发布 ✓')
      if (expanded !== null) openDetail(expanded)
    } catch (e) { setDetailErr(apiErrText(e)) }
  }

  const archive = async (id: number) => {
    setError('')
    try {
      await api(`/motifs/${id}`, { method: 'PUT', body: JSON.stringify({ status: 'archived' }) })
      setExpanded(null)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const removeOne = async (id: number) => {
    setError('')
    try {
      await api(`/motifs/${id}`, { method: 'DELETE' })
      setExpanded(null)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const copyOne = (text: string) => {
    navigator.clipboard?.writeText(text)
    setMsg('已复制 ✓')
  }

  return (
    <div>
      <ErrorLine text={error} />
      {/* 新建母题 */}
      <div className="mb-4 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
        <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">
          一个主题 = 一个母题，派生四线（口播/公众号/小红书/朋友圈）——各线独立走生成流
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="母题（如：企业 AI 落地验收卡壳的真因）*"
            className="min-w-0 flex-1 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="切角/备注（可选）"
            className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <button onClick={create} disabled={busy || !title.trim()}
            className="rounded-full bg-[#0a2f24] px-3.5 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90 disabled:opacity-40">
            <Plus size={13} className="inline" /> 建母题
          </button>
        </div>
      </div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12.5px] text-emerald-700">{msg}</div>}

      {!loaded ? null : rows.length === 0 ? (
        <EmptyState icon={Lightbulb} title="还没有母题"
          desc="好素材别只喂一条线——建母题后一键派生四线，产量×3，且跨线不重做。" />
      ) : (
        <div className="space-y-2">
          {rows.map((m) => {
            const open = expanded === m.id
            return (
              <div key={m.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => openDetail(m.id)}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{m.title}</span>
                  {LINES.map((L) => (
                    <span key={L.key} className="flex shrink-0 items-center gap-1 text-[11px] text-slate-400">
                      <span className={`size-1.5 rounded-full ${DOT_CLS(m.counts?.[L.key]?.n || 0)}`} />
                      {L.label}
                    </span>
                  ))}
                  <span className="shrink-0 text-[11px] text-slate-400">{relTime(m.created_at || '')}</span>
                </div>
                {m.note && <div className="mt-0.5 truncate pl-1 text-[12px] text-slate-400">{m.note}</div>}
                {open && (
                  <div className="mt-3 space-y-2 border-t border-[#E8ECF0] pt-3" onClick={(e) => e.stopPropagation()}>
                    <ErrorLine text={detailErr} />
                    {!detail ? <div className="text-[12.5px] text-slate-300">读详情中…</div> : (
                      LINES.map((L) => {
                        const st = detail.counts?.[L.key] || { n: 0, items: [] }
                        return (
                          <div key={L.key} className="rounded-lg bg-slate-50/60 px-3 py-2">
                            <div className="flex items-center gap-2 text-[13px]">
                              <span className="font-medium text-slate-700">{L.label}</span>
                              <span className={`num-serif text-slate-500`}>{st.n}</span>
                              <span className="ml-auto flex items-center gap-2">
                                {L.key === 'script' && <button onClick={() => derive(m.id, 'script')} className="btn-ghost btn-xs">派生到口播</button>}
                                {L.key === 'note' && <button onClick={() => derive(m.id, 'note')} className="btn-ghost btn-xs">派生到小红书</button>}
                                {L.key === 'moments' && <button onClick={() => derive(m.id, 'moments')} className="btn-ghost btn-xs">生成朋友圈文案</button>}
                                {L.key === 'article' && <button onClick={() => derive(m.id, 'article')} className="btn-ghost btn-xs"
                                  title="从该母题的终稿口播稿派生长文">从口播稿派生长文</button>}
                              </span>
                            </div>
                            {st.n > 0 && (
                              <div className="mt-1.5 space-y-1">
                                {st.items.map((it) => (
                                  <div key={`${L.key}-${it.id}`} className="flex flex-wrap items-center gap-2 text-[12px]">
                                    <span className="rounded-full bg-white px-1.5 py-px text-[10.5px] text-slate-500">{ST_LABEL[it.status] || it.status}</span>
                                    <span className="min-w-0 flex-1 truncate text-slate-600">{it.title || `#${it.id}`}</span>
                                    {L.key === 'moments' && (it.posts || []).map((p, i) => (
                                      <button key={i} onClick={() => copyOne(p.body)}
                                        className="text-[11px] text-sky-600 hover:underline">复制文案{i + 1}</button>
                                    ))}
                                    {L.key === 'moments' && it.status !== 'published' && (
                                      <button onClick={() => markPublished(it.id)} className="text-[11px] text-emerald-600 hover:underline">标记已发</button>
                                    )}
                                  </div>
                                ))}
                                {(L.key === 'moments' ? (st.items[0]?.posts?.[0]?.body) : null) && (
                                  <div className="rounded bg-white px-2 py-1.5 text-[12px] leading-relaxed text-slate-600">
                                    {st.items[0].posts?.[0]?.body}
                                    <span className="ml-1 text-[10.5px] text-slate-300">
                                      配图：{st.items[0].posts?.[0]?.visual}
                                    </span>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })
                    )}
                    <div className="flex items-center gap-2 pt-1">
                      <button onClick={() => archive(m.id)} className="btn-ghost btn-xs">归档</button>
                      <button onClick={() => removeOne(m.id)} className="btn-ghost btn-xs text-red-500">删除</button>
                      <span className="text-[11px] text-slate-300">派生过的母题只能归档不能删</span>
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
