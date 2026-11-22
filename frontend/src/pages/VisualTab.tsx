import { useCallback, useEffect, useState } from 'react'
import { Images, Trash2 } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine } from '../components'

/**
 * 视觉工厂（M7 2026-11-22）：封面模板库（五套代码渲染模板预览+设默认）+ 图文卡片工厂
 * （来源笔记/素材 → LLM 页面规划 → 逐页统一母版渲染）。零图片模型依赖。
 */

interface TplRow { key: string; name: string; is_default: boolean; preview_url: string }

export function CoverTemplates() {
  const [items, setItems] = useState<TplRow[] | null>(null)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api<{ items: TplRow[] }>('/visual/templates')
      .then((d) => setItems(d.items || []))
      .catch((e) => setError(apiErrText(e)))
  }, [])
  useEffect(load, [load])

  const setDefault = async (key: string) => {
    setError(''); setMsg('')
    try {
      await api('/visual/templates/default', { method: 'PUT', body: JSON.stringify({ key }) })
      setMsg(`默认封面模板已切换（新笔记与重渲染即生效）`)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  return (
    <div>
      <ErrorLine text={error} />
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12.5px] text-emerald-700">{msg}</div>}
      <div className="mb-3 text-[12.5px] text-slate-500">
        五套封面模板（1080×1440，共用品牌色与署名）——笔记生成与「重渲染封面」按默认模板出图。
      </div>
      {!items ? <div className="text-sm text-slate-300">模板加载中…</div> : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
          {items.map((t) => (
            <div key={t.key} className="rounded-xl border border-[#E8ECF0] bg-white p-2">
              <img src={t.preview_url} alt={t.name} className="w-full rounded-lg border border-slate-100" />
              <div className="mt-1.5 flex items-center gap-1.5">
                <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-slate-800">{t.name}</span>
                {t.is_default ? (
                  <span className="badge shrink-0 bg-emerald-100 text-emerald-700">默认</span>
                ) : (
                  <button onClick={() => setDefault(t.key)} className="btn-ghost btn-xs shrink-0">设默认</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface DeckRow {
  id: number
  title: string
  status: string
  deck_state: string
  pages: number
  source: { kind?: string; id?: number }
  created_at: string
}
interface DeckPage { idx: number; title: string; role: string; url: string }

const DSTATE: Record<string, { label: string; cls: string }> = {
  drafting: { label: '待生成', cls: 'bg-slate-100 text-slate-500' },
  generating: { label: '生成中', cls: 'bg-sky-100 text-sky-700' },
  ready: { label: '完成', cls: 'bg-emerald-100 text-emerald-700' },
}

export function VisualDecks() {
  const [items, setItems] = useState<DeckRow[] | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [pages, setPages] = useState<DeckPage[]>([])
  // 发起表单
  const [srcKind, setSrcKind] = useState('note')
  const [srcId, setSrcId] = useState('')
  const [pageCount, setPageCount] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api<{ items: DeckRow[] }>('/visual/decks')
      .then((d) => setItems(d.items || []))
      .catch((e) => setError(apiErrText(e)))
  }, [])
  useEffect(load, [load])

  const create = async () => {
    setError(''); setMsg(''); setBusy(true)
    try {
      const id = parseInt(srcId, 10)
      if (!Number.isFinite(id) || id <= 0) { setError('来源 id 必须是正整数'); return }
      const pc = parseInt(pageCount, 10)
      const d = await api<{ deck_id: number; job_id: number }>('/visual/decks', {
        method: 'POST',
        body: JSON.stringify({ source_kind: srcKind, source_id: id, page_count: Number.isFinite(pc) ? pc : 0 }),
      })
      await waitJob(d.job_id, (j) => setMsg(`生成中：${j.progress}% ${j.message || ''}`))
      setSrcId(''); setPageCount('')
      setMsg('图文卡完成 ✓')
      load()
    } catch (e) { setError(apiErrText(e)) } finally { setBusy(false) }
  }

  const openOne = async (id: number) => {
    if (expanded === id) { setExpanded(null); setPages([]); return }
    setExpanded(id); setPages([])
    try {
      const d = await api<{ pages: DeckPage[] }>(`/visual/decks/${id}`)
      setPages(d.pages || [])
    } catch (e) { setError(apiErrText(e)) }
  }

  const removeOne = async (id: number) => {
    setError('')
    try {
      await api(`/visual/decks/${id}`, { method: 'DELETE' })
      if (expanded === id) setExpanded(null)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  return (
    <div>
      <ErrorLine text={error} />
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12.5px] text-emerald-700">{msg}</div>}
      {/* 发起 */}
      <div className="mb-4 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
        <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">
          发起图文卡：来源=笔记稿或素材（有正文），AI 规划 3~10 页、逐页渲染（右键页图可保存）
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={srcKind} onChange={(e) => setSrcKind(e.target.value)}
            className="rounded-lg border border-[#E8ECF0] bg-white px-2.5 py-1.5 text-[13px] outline-none focus:border-emerald-400">
            <option value="note">来源：笔记稿 id</option>
            <option value="material">来源：素材 id</option>
          </select>
          <input value={srcId} onChange={(e) => setSrcId(e.target.value)} placeholder="id（列表行上可见）"
            className="w-40 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <input value={pageCount} onChange={(e) => setPageCount(e.target.value)} placeholder="页数（空=自动）"
            className="w-32 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <button onClick={create} disabled={busy || !srcId.trim()}
            className="rounded-full bg-[#0a2f24] px-3.5 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90 disabled:opacity-40">
            生成图文卡
          </button>
        </div>
      </div>

      {!items ? <div className="text-sm text-slate-300">载入中…</div> : items.length === 0 ? (
        <EmptyState icon={Images} title="还没有图文卡" desc="发一篇笔记或挑一条素材，上面发起——AI 拆页、逐页渲染成小红书图文。" />
      ) : (
        <div className="space-y-2">
          {items.map((d) => {
            const st = DSTATE[d.deck_state] || { label: d.deck_state, cls: 'bg-slate-100 text-slate-500' }
            const open = expanded === d.id
            return (
              <div key={d.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => openOne(d.id)}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.label}</span>
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{d.title}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">{d.pages > 0 ? `${d.pages} 页` : ''}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">{(d.created_at || '').slice(0, 10)}</span>
                </div>
                {open && (
                  <div className="mt-3 space-y-2 border-t border-[#E8ECF0] pt-3" onClick={(e) => e.stopPropagation()}>
                    {pages.length === 0 ? (
                      <div className="text-[12.5px] text-slate-300">还没有页图（生成完成后这里逐页预览）</div>
                    ) : (
                      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                        {pages.map((p) => (
                          <a key={p.idx} href={p.url} target="_blank" rel="noreferrer" className="block">
                            <img src={p.url} alt={p.title} className="w-full rounded-lg border border-slate-100" />
                            <div className="mt-1 truncate text-[11px] text-slate-400">P{p.idx + 1} {p.title}</div>
                          </a>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center gap-2 pt-1">
                      <button onClick={() => removeOne(d.id)} className="btn-ghost btn-xs text-red-500">
                        <Trash2 size={12} className="inline" /> 删除
                      </button>
                      <span className="text-[11px] text-slate-300">页图点开可原图保存</span>
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
