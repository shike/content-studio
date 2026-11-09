import { useCallback, useEffect, useMemo, useState } from 'react'
import { BookOpen, ChevronDown, ChevronRight, PenLine, RefreshCw, X } from 'lucide-react'
import { api, apiErrText, KBTopic, waitJob } from '../api'
import { Busy, ChipRow, EmptyState, ErrorLine, PageHeader } from '../components'

const AUDIENCES = ['全部', '老板', '转行者']
const STATUS_META: Record<string, { label: string; cls: string }> = {
  new: { label: '待选', cls: 'bg-emerald-100 text-emerald-800' },
  selected: { label: '已选用', cls: 'bg-sky-100 text-sky-800' },
  written: { label: '已成文', cls: 'bg-slate-100 text-slate-600' },
  dismissed: { label: '不做', cls: 'bg-slate-100 text-slate-400' },
}

export default function KbTopics() {
  const [topics, setTopics] = useState<KBTopic[]>([])
  const [status, setStatus] = useState('new')
  const [audience, setAudience] = useState('全部')
  const [booted, setBooted] = useState(false)
  const [error, setError] = useState('')
  const [syncBusy, setSyncBusy] = useState('')
  const [rowBusy, setRowBusy] = useState<Record<number, string>>({})
  const [expanded, setExpanded] = useState<number | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const load = useCallback(async (st: string) => {
    const r = await api<{ topics: KBTopic[] }>(`/articles/kb-topics?status=${st}`)
    setTopics(r.topics || [])
  }, [])

  const reload = useCallback(async () => {
    await load(status)
  }, [load, status])

  useEffect(() => {
    reload()
      .then(() => setBooted(true))
      .catch((e) => { setError(apiErrText(e)); setBooted(true) })
  }, [reload])

  const runTraversal = () => {
    setError('')
    setSyncBusy('知识库遍历：提交任务')
    api<{ job_id: number }>('/articles/kb-topics', { method: 'POST' })
      .then(({ job_id }) => waitJob(job_id, (j) => setSyncBusy(`知识库遍历：${j.progress}% ${j.message}`)))
      .then(() => { setSyncBusy(''); return load('new') })
      .then(() => setStatus('new'))
      .then(() => reload())
      .catch((e) => { setSyncBusy(''); setError(apiErrText(e)) })
  }

  const selectTopic = (t: KBTopic) => {
    setError('')
    setRowBusy((m) => ({ ...m, [t.id]: '选用成文：提交任务' }))
    api<{ job_id: number; article_id: number }>(`/articles/kb-topics/${t.id}/select`, { method: 'POST' })
      .then(({ job_id, article_id }) => waitJob(job_id, (j) => setRowBusy((m) => ({ ...m, [t.id]: `成文：${j.progress}% ${j.message}` }))))
      .then(() => { setRowBusy((m) => ({ ...m, [t.id]: '' })); return load(status) })
      .catch((e) => { setRowBusy((m) => ({ ...m, [t.id]: '' })); setError(apiErrText(e)) })
  }

  const dismissTopic = (t: KBTopic) => {
    setRowBusy((m) => ({ ...m, [t.id]: '处理中' }))
    api(`/articles/kb-topics/${t.id}/dismiss`, { method: 'POST' })
      .then(() => load(status))
      .catch((e) => { setRowBusy((m) => ({ ...m, [t.id]: '' })); setError(apiErrText(e)) })
  }

  const groups = useMemo(() => {
    const g: Record<string, KBTopic[]> = {}
    for (const t of topics) {
      const key = t.folder_name || '未分组'
      ;(g[key] = g[key] || []).push(t)
    }
    return Object.entries(g).sort((a, b) => b[1].length - a[1].length)
  }, [topics])

  const filtered = audience === '全部' ? topics : topics.filter((t) => t.audience === audience)

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <PageHeader
        icon={BookOpen}
        title="知识库选题"
        desc="体系化遍历 ima 知识库，从真货文档提炼带判断、绑定依据文档的选题——选用后自动成文，进报题与长文草稿箱。"
        actions={(
          <button onClick={runTraversal} disabled={!!syncBusy}
            className="btn-accent justify-center disabled:opacity-40">
            <RefreshCw size={14} /> {syncBusy ? '遍历中…' : '开始遍历知识库'}
          </button>
        )}
      />

      {syncBusy && <div className="mt-3"><Busy text={syncBusy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}
      {!booted && <div className="mt-3"><Busy text="加载中…" /></div>}

      {booted && (
        <>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <ChipRow
              chips={[{ key: 'new', label: '待选' }, { key: 'selected', label: '已选用' }, { key: 'dismissed', label: '不做' }, { key: 'written', label: '已成文' }]}
              active={status}
              onChange={(k) => { setStatus(k); load(k).catch((e) => setError(apiErrText(e))) }}
            />
            <div className="flex items-center gap-1.5">
              {AUDIENCES.map((f) => (
                <button key={f} onClick={() => setAudience(f)}
                  className={`rounded-full border px-2.5 py-0.5 text-[11px] transition ${
                    audience === f ? 'border-emerald-400 bg-emerald-100 font-medium text-emerald-800' : 'border-slate-200 text-slate-500 hover:border-slate-300'
                  }`}>{f}</button>
              ))}
            </div>
          </div>

          {topics.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                icon={BookOpen}
                title={status === 'new' ? '选题池是空的' : '该状态下没有选题'}
                desc={status === 'new' ? '点右上「开始遍历知识库」，系统会扫完全部真货文档并提炼选题。' : ''}
              />
            </div>
          ) : (
            <div className="mt-4 space-y-5">
              {groups.map(([folder, items]) => {
                const vis = audience === '全部' ? items : items.filter((t) => t.audience === audience)
                if (vis.length === 0) return null
                const isCollapsed = collapsed[folder]
                return (
                  <div key={folder} className="rounded-xl border border-slate-200 bg-white">
                    <button
                      onClick={() => setCollapsed((m) => ({ ...m, [folder]: !m[folder] }))}
                      className="flex w-full items-center gap-2 px-4 py-2.5 text-left"
                    >
                      {isCollapsed ? <ChevronRight size={14} className="text-slate-400" /> : <ChevronDown size={14} className="text-slate-400" />}
                      <span className="text-[13px] font-semibold text-slate-700">{folder}</span>
                      <span className="text-[12px] text-slate-400">{vis.length} 个</span>
                    </button>
                    {!isCollapsed && (
                      <div className="divide-y divide-slate-100 border-t border-slate-100">
                        {vis.map((t) => {
                          const meta = STATUS_META[t.status] || { label: t.status, cls: 'bg-slate-100 text-slate-500' }
                          const open = expanded === t.id
                          return (
                            <div key={t.id} className="px-4 py-2.5 hover:bg-slate-50/60">
                              <div className="flex items-center gap-2">
                                <button
                                  onClick={() => setExpanded(open ? null : t.id)}
                                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                                >
                                  {open ? <ChevronDown size={13} className="shrink-0 text-slate-300" /> : <ChevronRight size={13} className="shrink-0 text-slate-300" />}
                                  <span className="truncate text-[13px] text-slate-800">{t.title}</span>
                                </button>
                                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${meta.cls}`}>{meta.label}</span>
                                {t.status === 'new' && (
                                  <span className="flex shrink-0 items-center gap-1">
                                    <button
                                      onClick={(e) => { e.stopPropagation(); selectTopic(t) }}
                                      disabled={!!rowBusy[t.id]}
                                      className="btn-accent btn-xs"
                                      title="建草稿并按知识库素材成文"
                                    >
                                      <PenLine size={12} /> 选用成文
                                    </button>
                                    <button
                                      onClick={(e) => { e.stopPropagation(); dismissTopic(t) }}
                                      disabled={!!rowBusy[t.id]}
                                      className="rounded p-1 text-slate-300 hover:text-red-500"
                                      title="不做"
                                    >
                                      <X size={13} />
                                    </button>
                                  </span>
                                )}
                              </div>
                              {open && (
                                <div className="mt-2 space-y-2 rounded-lg bg-slate-50 p-3 text-[12px] leading-relaxed text-slate-600">
                                  <div><span className="font-semibold text-slate-500">判断：</span>{t.judgment}</div>
                                  <div><span className="font-semibold text-slate-500">读者：</span>{t.audience}　<span className="font-semibold text-slate-500">为什么值得写：</span>{t.why}</div>
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    <span className="shrink-0 font-semibold text-slate-500">依据文档：</span>
                                    {(t.docs ?? []).map((d, i) => (
                                      <span key={i} className="rounded-full border border-emerald-200 bg-emerald-50/60 px-2 py-0.5 text-emerald-700">{d.title}</span>
                                    ))}
                                  </div>
                                  {rowBusy[t.id] && <div className="pt-1"><Busy text={rowBusy[t.id]} /></div>}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
