import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, FileVideo, Inbox, Lightbulb, Scissors, Sparkles, Trash2 } from 'lucide-react'
import { api, waitJob } from '../api'
import { Busy, ChipRow, EmptyState, ErrorLine, PageHeader, relTime } from '../components'
import type { SectionKey } from '../App'

interface BenchCounts {
  all: number
  pending: number
  todo: number
  done: number
  ignored: number
  online: number
  local: number
}

interface Benchmark {
  id: number
  scan_status?: string
  url: string
  author: string
  title: string
  transcript: string
  analysis: Record<string, any>
  stats: Record<string, any>
  source: string
  created_at?: string
  hot?: boolean
  digg?: number | null
}

const analyzed = (b: Benchmark) =>
  Boolean(b.analysis && (b.analysis.hook || b.analysis.structure || b.analysis.score != null))

export default function Benchmarks({ onNavigate }: { onNavigate?: (k: SectionKey) => void }) {
  const [items, setItems] = useState<Benchmark[]>([])
  const [url, setUrl] = useState('')
  const [filter, setFilter] = useState('')
  const [author, setAuthor] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [showTranscript, setShowTranscript] = useState(false)
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<BenchCounts>({
    all: 0, pending: 0, todo: 0, done: 0, ignored: 0, online: 0, local: 0,
  })
  const [page, setPage] = useState(0)
  const fileRef = useRef<HTMLInputElement>(null)
  const bootedRef = useRef(false)
  const PAGE_SIZE = 20

  const fetchPage = useCallback(async (f: string, pg: number, au: string = author) => {
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE), offset: String(pg * PAGE_SIZE),
    })
    if (f === 'todo' || f === 'done') params.set('analyzed', f)
    if (f === 'pending' || f === 'ignored') params.set('scan_status', f)
    // 数据里 source 的值是 douyin/local，「在线」= douyin
    if (f === 'online') params.set('source', 'douyin')
    if (f === 'local') params.set('source', 'local')
    if (au) params.set('author', au)
    const b = await api<{ items: Benchmark[]; total: number; counts: BenchCounts }>(
      `/benchmarks?${params.toString()}`)
    setItems(b.items)
    setTotal(b.total)
    setCounts(b.counts)
  }, [author])

  const reload = useCallback(async () => {
    await fetchPage(filter, page)
  }, [fetchPage, filter, page])

  useEffect(() => {
    (async () => {
      try {
        const first = await api<{ counts: BenchCounts }>('/benchmarks?limit=1')
        bootedRef.current = true
        if (first.counts.pending > 0 && filter === '' && page === 0) {
          setFilter('pending')
          await fetchPage('pending', 0)
        } else {
          await reload()
        }
      } catch (e) {
        setError(String(e))
      }
    })()
  }, [reload])

  useEffect(() => {
    setShowTranscript(false)
  }, [expandedId])

  const deleteBenchmark = (id: number) =>
    guard(async () => {
      const r = await api<{ removed_candidates: number }>(`/benchmarks/${id}`, { method: 'DELETE' })
      setConfirmDel(null)
      if (r.removed_candidates > 0) {
        setBusy(`已删除样本及其 ${r.removed_candidates} 条待审候选`)
        setTimeout(() => setBusy(''), 3000)
      }
      await reload()
    })

  const guard = async (fn: () => Promise<void>) => {
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy('')
    }
  }

  const [rowBusy, setRowBusy] = useState<number | null>(null)
  const resolve = (id: number, action: 'approve' | 'ignore' | 'pending') =>
    guard(async () => {
      setRowBusy(id)
      try {
        await api(`/benchmarks/${id}/resolve`, {
          method: 'POST',
          body: JSON.stringify({ action }),
        })
        if (action === 'approve') setBusy('已批准，排队拆解中')
        await reload()
      } finally {
        setRowBusy(null)
      }
    })

  const resolveAllPending = (action: 'approve' | 'ignore') =>
    guard(async () => {
      const r = await api<{ resolved: number; job_ids: number[] }>('/benchmarks/resolve-pending', {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      setBusy(
        action === 'approve'
          ? `已批准 ${r.resolved} 条，全部排队拆解中`
          : `已忽略 ${r.resolved} 条`,
      )
      setTimeout(() => setBusy(''), 4000)
      await reload()
    })

  const analyzeOnline = () =>
    guard(async () => {
      if (!url.trim()) return
      const r = await api<{ job_id: number; crawl_warning?: string }>('/analyze/video', {
        method: 'POST',
        body: JSON.stringify({ url: url.trim() }),
      })
      if (r.crawl_warning) setBusy(r.crawl_warning)
      setUrl('')
      await waitJob(r.job_id, (j) => setBusy(`拆解中：${j.progress}% ${j.message}`))
      await reload()
    })

  const analyzeLocal = (file: File) =>
    guard(async () => {
      const form = new FormData()
      form.append('file', file)
      setBusy('上传中…')
      const res = await fetch('/api/analyze/local', { method: 'POST', body: form })
      if (!res.ok) throw new Error(`HTTP ${res.status} ${await res.text()}`)
      const r = await res.json()
      await waitJob(r.job_id, (j) => setBusy(`拆解中：${j.progress}% ${j.message}`))
      await reload()
    })

  const authorOptions = Array.from(new Set(items.map((b) => b.author).filter(Boolean)))
  const filtered = items
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const changeFilter = (k: string) => {
    setFilter(k)
    setPage(0)
  }
  const detail = items.find((b) => b.id === expandedId)
  const a = detail?.analysis || {}

  return (
    <div>
      <PageHeader
        icon={Scissors}
        title="拆解库"
        desc="贴链接 / 传本地视频，自动下载转写拆解，沉淀选题候选"
      />

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {/* hero：拆解入口 */}
      <div className="card border-sky-200/70 bg-gradient-to-br from-sky-50/80 to-white p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Download size={15} className="absolute top-1/2 left-3 -translate-y-1/2 text-sky-500" />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && analyzeOnline()}
              placeholder="粘贴抖音视频分享链接，回车自动拆解"
              className="input border-sky-200 pl-9"
            />
          </div>
          <button onClick={analyzeOnline} disabled={!url.trim()} className="btn-accent justify-center disabled:opacity-40 sm:w-32">
            在线拆解
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="video/*,audio/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) analyzeLocal(f)
              e.target.value = ''
            }}
          />
          <button onClick={() => fileRef.current?.click()} className="btn-ghost justify-center sm:w-36">
            <FileVideo size={15} /> 本地视频拆解
          </button>
        </div>
        <div className="mt-2 text-[11px] text-slate-400">
          拆解产出：钩子类型 / 叙事结构 / 可复刻点，并自动沉淀 1 条选题候选到选题库
        </div>
      </div>

      <div className="mt-4">
        <ChipRow
          active={filter}
          onChange={changeFilter}
          chips={[
            { key: 'pending', label: '待定夺', count: counts.pending },
            { key: 'todo', label: '待拆解', count: counts.todo },
            { key: 'done', label: '已拆解', count: counts.done },
            { key: 'ignored', label: '已忽略', count: counts.ignored },
            { key: '', label: '全部', count: counts.all },
          ]}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-400">来源</span>
        <select value={filter === 'online' ? 'online' : filter === 'local' ? 'local' : ''} onChange={(e) => changeFilter(e.target.value)} className="select max-w-28 text-xs">
          <option value="">全部来源</option>
          <option value="online">在线</option>
          <option value="local">本地</option>
        </select>
        <span className="text-slate-400">作者</span>
        <select value={author} onChange={(e) => { setAuthor(e.target.value); setPage(0) }} className="select max-w-40 text-xs">
          <option value="">全部作者</option>
          {authorOptions.map((a2) => (
            <option key={a2} value={a2}>@{a2}</option>
          ))}
        </select>
      </div>

      {filter === 'pending' && counts.pending > 0 && (
        <div className="card mt-4 flex flex-wrap items-center gap-2 border-amber-200 bg-amber-50/60 px-4 py-3 text-[13px] text-amber-800">
          <span>扫描发现 {counts.pending} 条待定夺——判断有价值再拆，省 LLM 额度：</span>
          <button onClick={() => resolveAllPending('approve')} className="btn-accent btn-xs">全部拆解</button>
          <button onClick={() => resolveAllPending('ignore')} className="btn-ghost btn-xs">全部忽略</button>
        </div>
      )}

      <div className="card mt-4 overflow-hidden p-0">
        {filtered.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Inbox}
              title="还没有拆解样本"
              desc="贴一条对标视频链接，或上传本地视频，系统自动转写并拆解。对标账号的批量扫描在「同行监测」页。"
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((b) => {
              const exp = expandedId === b.id
              return (
                <div key={b.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => setExpandedId(exp ? null : b.id)}
                    className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                  >
                    <span
                      className={`badge shrink-0 ${b.source === 'douyin' ? 'bg-blue-100 text-blue-700' : 'bg-teal-100 text-teal-700'}`}
                    >
                      {b.source === 'douyin' ? '在线' : '本地'}
                    </span>
                    {b.hot && (
                      <span className="badge shrink-0 bg-orange-100 text-orange-700" title="点赞 ≥ 作者中位数×3">🔥 爆款</span>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-zinc-900">
                        {b.title || `样本 #${b.id}`}
                        {b.author && <span className="ml-1.5 text-xs font-normal text-slate-400">@{b.author}</span>}
                      </div>
                      {typeof b.digg === 'number' && (
                        <div className="mt-0.5 text-xs text-slate-500">
                          赞 <b className="tabular-nums text-slate-600">{b.digg.toLocaleString()}</b>
                          {(b.stats?.comment || b.stats?.share) && (
                            <span className="ml-1.5">· 评 {b.stats.comment ?? '—'} · 转 {b.stats.share ?? '—'}</span>
                          )}
                        </div>
                      )}
                      {b.transcript && (
                        <div className="mt-0.5 truncate text-xs text-slate-400">{b.transcript.slice(0, 60)}</div>
                      )}
                    </div>
                    <span
                      className={`badge shrink-0 ${
                        b.scan_status === 'ignored' ? 'bg-slate-200 text-slate-500'
                          : analyzed(b) ? 'bg-emerald-100 text-emerald-700'
                          : b.scan_status === 'pending' ? 'bg-amber-100 text-amber-800'
                          : 'bg-sky-100 text-sky-700'
                      }`}
                    >
                      {b.scan_status === 'ignored' ? '已忽略'
                        : analyzed(b) ? '已拆解'
                        : b.scan_status === 'pending' ? '待定夺'
                        : '待拆解'}
                    </span>
                    {analyzed(b) && (
                      <button
                        onClick={(e) => { e.stopPropagation(); resolve(b.id, 'approve') }}
                        className="btn-ghost btn-xs shrink-0 text-slate-500"
                        title="用当前管线重新转写拆解一次"
                      >
                        重新拆解
                      </button>
                    )}
                    {b.scan_status === 'pending' && (
                      <button
                        onClick={(e) => { e.stopPropagation(); resolve(b.id, 'approve') }}
                        disabled={rowBusy === b.id}
                        className="btn-accent btn-xs shrink-0 disabled:opacity-40"
                      >
                        {rowBusy === b.id ? '排队中…' : '拆解'}
                      </button>
                    )}
                    {b.scan_status === 'pending' && (
                      <button
                        onClick={(e) => { e.stopPropagation(); resolve(b.id, 'ignore') }}
                        className="btn-ghost btn-xs shrink-0 text-slate-400"
                      >
                        忽略
                      </button>
                    )}
                    {b.scan_status === 'ignored' && (
                      <>
                        <button
                          onClick={(e) => { e.stopPropagation(); resolve(b.id, 'pending') }}
                          className="btn-ghost btn-xs shrink-0 text-slate-500"
                          title="恢复到待定夺"
                        >
                          恢复
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); resolve(b.id, 'approve') }}
                          className="btn-accent btn-xs shrink-0"
                        >
                          拆解
                        </button>
                      </>
                    )}
                    {confirmDel === b.id ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteBenchmark(b.id) }}
                        className="btn-danger btn-xs shrink-0"
                      >
                        确认删除
                      </button>
                    ) : (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmDel(b.id)
                          setTimeout(() => setConfirmDel((c) => (c === b.id ? null : c)), 3000)
                        }}
                        title="删除样本"
                        className="btn-ghost btn-xs shrink-0 text-slate-300 hover:text-red-500"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                    {typeof b.analysis?.score === 'number' && (
                      <span className="w-9 shrink-0 text-right text-sm font-bold tabular-nums text-sky-600">
                        {b.analysis.score}
                      </span>
                    )}
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      {relTime(b.created_at)}
                    </span>
                  </div>

                  {exp && (
                    <div className="grid gap-5 border-t border-sky-100 px-5 py-4 md:grid-cols-2">
                      {/* 左：主体内容 */}
                      <div className="space-y-4">
                        {a.hook && (
                          <div>
                            <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
                              <Sparkles size={12} className="text-sky-500" /> 钩子（{a.hook.type}）
                            </div>
                            <div className="rounded-lg border-l-2 border-sky-400 bg-sky-50/70 px-4 py-2.5 text-sm text-zinc-800">
                              {a.hook.position_text}
                            </div>
                          </div>
                        )}
                        {Array.isArray(a.structure) && a.structure.length > 0 && (
                          <div>
                            <div className="mb-1.5 text-xs font-semibold text-slate-500">叙事结构</div>
                            <ol className="space-y-2">
                              {a.structure.map((st: any, i: number) => (
                                <li key={i} className="flex gap-3 text-[13px] leading-relaxed">
                                  <span className="grid size-5 shrink-0 place-items-center rounded-full bg-slate-900 text-[10px] font-bold text-sky-400">
                                    {i + 1}
                                  </span>
                                  <span>
                                    <span className="font-medium text-zinc-800">{st.step}</span>
                                    <span className="text-slate-600"> — {st.content}</span>
                                  </span>
                                </li>
                              ))}
                            </ol>
                          </div>
                        )}
                        {Array.isArray(a.replicable_points) && a.replicable_points.length > 0 && (
                          <div>
                            <div className="mb-1.5 text-xs font-semibold text-slate-500">可复刻点</div>
                            <ul className="space-y-1.5">
                              {a.replicable_points.map((p: string, i: number) => (
                                <li key={i} className="flex gap-2 text-[13px] text-slate-700">
                                  <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-emerald-400" />
                                  {p}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                      {/* 右：元信息与联动 */}
                      <div className="space-y-4">
                        {a.topic_value && (
                          <div className="rounded-lg bg-slate-50 px-3.5 py-2.5 text-[13px] text-slate-600">
                            选题价值：{a.topic_value}
                          </div>
                        )}
                        {detail?.transcript && (
                          <div>
                            <button
                              onClick={() => setShowTranscript((v) => !v)}
                              className="text-xs font-semibold text-slate-500 hover:text-slate-700"
                            >
                              转写全文 {showTranscript ? '▲' : '▼'}
                            </button>
                            {showTranscript && (
                              <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-50 p-3.5 text-[13px] leading-relaxed whitespace-pre-wrap text-slate-700">
                                {detail.transcript}
                              </pre>
                            )}
                          </div>
                        )}
                        {detail?.analysis?.topic_candidate && (
                          <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50/60 px-3.5 py-2.5 text-[13px] text-emerald-800">
                            <Lightbulb size={14} />
                            已沉淀选题候选「{detail.analysis.topic_candidate.title}」
                            {onNavigate && (
                              <button onClick={() => onNavigate('topics')} className="ml-1 font-semibold underline hover:text-emerald-900">
                                去选题库定审 →
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-center gap-3 text-[13px] text-slate-500">
          <button
            onClick={() => setPage((x) => Math.max(0, x - 1))}
            disabled={page === 0}
            className="btn-ghost btn-xs disabled:opacity-40"
          >
            上一页
          </button>
          <span>
            第 {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} 条 · 共 {total} 条
          </span>
          <button
            onClick={() => setPage((x) => Math.min(pages - 1, x + 1))}
            disabled={page >= pages - 1}
            className="btn-ghost btn-xs disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
