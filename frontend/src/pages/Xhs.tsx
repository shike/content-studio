import { useCallback, useEffect, useState } from 'react'
import { Feather, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, PageHeader, relTime } from '../components'

/**
 * 小红书（PRD R5）：独立经营号，图文笔记先行——手动发起选题 → note_generate
 * 出笔记稿（标题/正文/标签/封面模板）→ 人工发布后登记。发布无官方接口，一律人工（R5.5）。
 */

interface NoteRow {
  id: number
  idea_id: number | null
  title: string
  body: string
  tags: string[]
  cover: string
  cover_url?: string
  status: string  // drafting|review|ready|published|failed
  material_ids: number[]
  publication: { url?: string; published_at?: string; mode?: string }
  created_at: string
}

const STATUS_BADGE: Record<string, string> = {
  drafting: 'bg-sky-100 text-sky-700', review: 'bg-amber-100 text-amber-800',
  ready: 'bg-emerald-100 text-emerald-700', published: 'bg-slate-200 text-slate-600',
  failed: 'bg-red-100 text-red-700',
}
const STATUS_LABEL: Record<string, string> = {
  drafting: '生成中', review: '待定稿', ready: '待发布', published: '已发布', failed: '生成失败',
}

function NewIdeaForm({ onCreated }: { onCreated: () => void }) {
  const [title, setTitle] = useState('')
  const [angle, setAngle] = useState('')
  const [materials, setMaterials] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setError('')
    setBusy(true)
    try {
      const mids = materials.split(/[,，\s]+/).map((x) => parseInt(x, 10)).filter((x) => !isNaN(x) && x > 0)
      const r = await api<{ id: number }>('/channels/xhs/ideas', {
        method: 'POST',
        body: JSON.stringify({ title, angle, material_ids: mids }),
      })
      // 直接建稿：手动发起即出稿（本期量小，一键到底）
      const c = await api<{ content_id: number; job_id: number }>(
        `/channels/xhs/ideas/${r.id}/compose`, { method: 'POST' })
      await waitJob(c.job_id, (j) => setError(`生成中：${j.progress}% ${j.message || ''}`))
      setTitle(''); setAngle(''); setMaterials('')
      onCreated()
    } catch (e) {
      setError(`发起失败：${apiErrText(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-4 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
      <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">
        发起一篇笔记（素材库 id 可选填——写作原料 + 引用追溯）
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="选题（如：企业知识库别急着买向量库）"
          className="min-w-0 flex-1 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        <input value={angle} onChange={(e) => setAngle(e.target.value)} placeholder="切角/要求（可选）"
          className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        <input value={materials} onChange={(e) => setMaterials(e.target.value)} placeholder="素材 id（逗号分隔，可选）"
          className="w-44 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        <button onClick={submit} disabled={busy || !title.trim()}
          className="rounded-full bg-[#0a2f24] px-3.5 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90 disabled:opacity-40">
          <Plus size={13} className="inline" /> {busy ? '生成中…' : '发起并生成'}
        </button>
      </div>
      {error && <div className="mt-2 text-[12.5px] text-slate-500">{error}</div>}
    </div>
  )
}

export default function Xhs() {
  const [notes, setNotes] = useState<NoteRow[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [edit, setEdit] = useState<{ id: number; title: string; body: string } | null>(null)

  const load = useCallback(() => {
    api<{ items: NoteRow[]; total: number }>('/channels/xhs/notes?limit=50')
      .then((d) => { setNotes(d.items || []); setTotal(d.total); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [])
  useEffect(() => { load() }, [load])

  const saveEdit = async () => {
    if (!edit) return
    setError('')
    try {
      await api(`/channels/xhs/notes/${edit.id}`, {
        method: 'PUT', body: JSON.stringify({ title: edit.title, body: edit.body }),
      })
      setEdit(null)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const rerenderCover = async (id: number) => {
    setError('')
    try {
      await api(`/channels/xhs/notes/${id}/cover`, { method: 'POST', body: JSON.stringify({}) })
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const removeOne = async (id: number) => {
    setError('')
    try {
      await api(`/channels/xhs/notes/${id}`, { method: 'DELETE' })
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  return (
    <div>
      <PageHeader icon={Feather} title="小红书"
        desc="独立经营号 · 图文笔记先行：从素材库/热点手动发起，AI 出笔记稿与封面，发布永远人工、发布后贴链接登记。" />
      <NewIdeaForm onCreated={load} />
      <ErrorLine text={error} />
      {!loaded ? null : notes.length === 0 ? (
        <EmptyState icon={Feather} title="还没有笔记稿"
          desc="上面发起第一篇：给一个选题（可带素材库 id），AI 出标题+正文+标签+封面，改完人工发布。" />
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2 text-[12.5px] text-slate-500">
            <span>共 {total} 篇</span>
            <span className="text-slate-300">·</span>
            <span>待发布 {notes.filter((n) => n.status === 'ready' || n.status === 'review').length}</span>
          </div>
          <div className="space-y-2">
            {notes.map((n) => {
              const open = expandedId === n.id
              return (
                <div key={n.id}
                  className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                  onClick={() => setExpandedId(open ? null : n.id)}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_BADGE[n.status] || 'bg-slate-100'}`}>
                      {STATUS_LABEL[n.status] || n.status}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{n.title || `笔记 #${n.id}`}</span>
                    {(n.tags || []).slice(0, 3).map((t) => (
                      <span key={t} className="shrink-0 rounded-full bg-rose-50 px-2 py-0.5 text-[11px] text-rose-600">#{t}</span>
                    ))}
                    <span className="shrink-0 text-[11px] text-slate-400">{relTime(n.created_at || '')}</span>
                    {n.publication?.url && (
                      <a href={n.publication.url} target="_blank" rel="noreferrer"
                        className="shrink-0 text-[12px] text-sky-600 hover:underline"
                        onClick={(e) => e.stopPropagation()}>已发布 ↗</a>
                    )}
                  </div>
                  {open && (
                    <div className="mt-3 space-y-2.5 border-t border-[#E8ECF0] pt-3" onClick={(e) => e.stopPropagation()}>
                      {edit?.id === n.id ? (
                        <div className="space-y-2">
                          <input value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })}
                            className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                          <textarea value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })}
                            rows={10}
                            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-[13px] leading-relaxed outline-none focus:border-emerald-400" />
                          <div className="flex gap-2">
                            <button onClick={saveEdit} className="btn-accent btn-sm">保存</button>
                            <button onClick={() => setEdit(null)} className="btn-ghost btn-sm">取消</button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-700">{n.body}</div>
                          <div className="flex flex-wrap gap-1.5">
                            {(n.tags || []).map((t) => (
                              <span key={t} className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] text-rose-600">#{t}</span>
                            ))}
                          </div>
                          {n.cover_url && (
                            <img src={n.cover_url} alt="封面" className="w-36 rounded-xl border border-slate-200" />
                          )}
                          <div className="flex flex-wrap gap-2 pt-1">
                            <button className="btn-accent btn-sm" onClick={() => setEdit({ id: n.id, title: n.title, body: n.body })}>
                              改稿
                            </button>
                            <button className="btn-ghost btn-sm" onClick={() => rerenderCover(n.id)}>
                              <RefreshCw size={12} className="inline" /> 重渲染封面
                            </button>
                            {n.status === 'ready' && (
                              <span className="btn-ghost btn-sm cursor-default">人工发布 → 回来贴链接（发布登记在数据域接入后开放本页表单）</span>
                            )}
                            <button className="btn-ghost btn-sm text-red-500" onClick={() => removeOne(n.id)}>
                              <Trash2 size={12} className="inline" /> 不用
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
