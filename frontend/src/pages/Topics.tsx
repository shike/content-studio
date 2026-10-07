import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Check,
  Copy,
  ChevronDown,
  Clock,
  Inbox,
  Lightbulb,
  ListChecks,
  Play,
  RotateCcw,
  SkipForward,
  Sparkles,
  Square,
  Trash2,
  CheckSquare,
  X,
} from 'lucide-react'
import { api, AUDIENCE_LABELS, SOURCE_LABELS, STATUS_LABELS, Topic } from '../api'
import { DetailBody } from '../topicDetail'
import { Busy, EmptyState, ErrorLine, PageHeader, Pager } from '../components'

const BADGE_SOURCE: Record<string, string> = {
  idea: 'bg-indigo-100 text-indigo-700',
  benchmark: 'bg-blue-100 text-blue-700',
  manual: 'bg-slate-200 text-slate-600',
}
const BADGE_STATUS: Record<string, string> = {
  draft: 'bg-slate-200 text-slate-600',
  approved: 'bg-emerald-100 text-emerald-700',
  rejected: 'bg-red-100 text-red-600',
  produced: 'bg-sky-100 text-sky-700',
}

function scoreColor(score: number | null) {
  if (score == null) return 'text-slate-300'
  if (score >= 8) return 'text-emerald-600'
  if (score >= 6) return 'text-sky-600'
  return 'text-slate-400'
}

function parseTs(iso: string) {
  // 后端时间戳是无时区后缀的 UTC，补 Z 再解析，避免按本地时区偏移
  return new Date(/[Zz]$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z').getTime()
}

function relTime(iso?: string) {
  if (!iso) return ''
  const ms = Date.now() - parseTs(iso)
  if (Number.isNaN(ms)) return ''
  if (ms < 0) return '刚刚'
  const min = Math.floor(ms / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} 天前`
  return `${Math.floor(d / 30)} 个月前`
}

function isNew(iso?: string) {
  if (!iso) return false
  const ms = Date.now() - parseTs(iso)
  return !Number.isNaN(ms) && ms >= 0 && ms < 24 * 3600 * 1000
}

export default function Topics() {
  const [all, setAll] = useState<Topic[]>([])
  const [view, setView] = useState<'queue' | 'table'>('table')
  const [booted, setBooted] = useState(false)

  /* 表格视图 */
  const [source, setSource] = useState('')
  const [status, setStatus] = useState('')
  const [audience, setAudience] = useState('')
  const [order, setOrder] = useState<'new' | 'score'>('new')
  const [resProg, setResProg] = useState<Record<number, { p: number; m: string }>>({})
  const [copiedHook, setCopiedHook] = useState<string | null>(null)
  const [confirmClean, setConfirmClean] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [detailCache, setDetailCache] = useState<Record<number, Topic>>({})
  const [batchMode, setBatchMode] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  /* 审阅队列 */
  const [skipped, setSkipped] = useState<Set<number>>(new Set())
  const [idx, setIdx] = useState(0)
  const [session, setSession] = useState({ approved: 0, rejected: 0, skipped: 0 })

  const [idea, setIdea] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const LIMIT = 20
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [drafts, setDrafts] = useState<Topic[]>([])

  const loadTable = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset), order })
    if (status) params.set('status', status)
    if (source) params.set('source', source)
    if (audience) params.set('audience', audience)
    const data = await api<{ items: Topic[]; total: number; counts?: Record<string, number> }>(`/topics?${params}`)
    setAll(data.items)
    setTotal(data.total)
    if (data.counts) setCounts(data.counts)
  }, [offset, status, source, audience, order])

  const loadDrafts = useCallback(async () => {
    const d = await api<{ items: Topic[] }>('/topics?status=draft&limit=200')
    setDrafts(d.items)
    return d.items
  }, [])

  const reload = useCallback(async () => {
    await Promise.all([loadTable(), loadDrafts()])
  }, [loadTable, loadDrafts])

  useEffect(() => {
    loadDrafts()
      .then((items) => {
        // 有待审 → 直接进审阅队列（一次会话只自动进入一次，退出后不强制）
        // Q：去掉强制弹出——待审队列只手动进入，页面默认表格视图
        setBooted(true)
      })
      .catch((e) => {
        setError(String(e))
        setBooted(true)
      })
  }, [loadDrafts])

  useEffect(() => {
    loadTable().catch((e) => setError(String(e)))
  }, [loadTable])

  const queue = useMemo(() => drafts.filter((t) => !skipped.has(t.id)), [drafts, skipped])

  useEffect(() => {
    if (drafts.length === 0) return
    let stop = false
    const tick = async () => {
      try {
        const d = await api<{ items: { type: string; status: string; progress: number; message: string; payload: Record<string, any> }[] }>('/jobs?limit=50&type=idea_research')
        const map: Record<number, { p: number; m: string }> = {}
        for (const j of d.items || []) {
          if (j.type !== 'idea_research' || (j.status !== 'running' && j.status !== 'queued')) continue
          const tid = j.payload?.topic_id
          if (tid) map[tid] = { p: j.progress || 0, m: j.message || '' }
        }
        if (!stop) setResProg(map)
      } catch { /* 静默，下轮再取 */ }
    }
    tick()
    const t = setInterval(tick, 5000)
    return () => { stop = true; clearInterval(t) }
  }, [drafts.length])
  const current = queue[idx] ?? null
  const totalAtStart = session.approved + session.rejected + session.skipped + queue.length
  const doneCount = session.approved + session.rejected + session.skipped

  const filtered = all // 服务端已按 status/source 筛选并分页
  const statusCount = useCallback((s: string) => (s ? counts[s] || 0 : Object.values(counts).reduce((a, b) => a + b, 0)), [counts])

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

  const fetchDetail = useCallback(
    async (id: number) => {
      const t = await api<Topic>(`/topics/${id}`)
      setDetailCache((c) => ({ ...c, [id]: t }))
      return t
    },
    [],
  )

  // 队列当前条目：拉全文（报告）
  useEffect(() => {
    if (view !== 'queue' || !current) return
    if (!detailCache[current.id]?.research_report)
      fetchDetail(current.id).catch((e) => setError(String(e)))
  }, [view, current?.id, detailCache, fetchDetail])

  // 表格展开行：拉全文
  useEffect(() => {
    if (expandedId == null) return
    const t = all.find((x) => x.id === expandedId)
    if (t && !detailCache[expandedId]?.research_report)
      fetchDetail(expandedId).catch((e) => setError(String(e)))
  }, [expandedId, all, detailCache, fetchDetail])

  const merged = useCallback(
    (id: number): Topic | null => {
      const live = all.find((t) => t.id === id)
      if (!live) return null
      return { ...detailCache[id], ...live }
    },
    [all, detailCache],
  )

  const submitIdea = () =>
    guard(async () => {
      if (!idea.trim()) return
      const r = await api<{ topic_id: number; job_id: number }>('/topics/ideas', {
        method: 'POST',
        body: JSON.stringify({ text: idea.trim() }),
      })
      setIdea('')
      setBusy('')
      await reload()
      // 深研进度在列表行内实时可见（轮询），完成后行内点「审阅」进入队列
    })

  /* 队列动作：定审/否决后条目自动离开队列，idx 天然指向下一条 */
  const decide = useCallback(
    (id: number, action: 'approve' | 'reject') =>
      guard(async () => {
        await api(`/topics/${id}/${action}`, { method: 'POST' })
        setDrafts((list) => list.filter((t) => t.id !== id))
        setAll((list) =>
          list.map((t) => (t.id === id ? { ...t, status: action === 'approve' ? 'approved' : 'rejected' } : t)),
        )
        loadTable().catch(() => {})
        setSession((s) => ({ ...s, [action === 'approve' ? 'approved' : 'rejected']: s[action === 'approve' ? 'approved' : 'rejected'] + 1 }))
      }),
    [loadTable],
  )

  const deleteTopic = (id: number) =>
    guard(async () => {
      await api(`/topics/${id}`, { method: 'DELETE' })
      await reload()
      setConfirmDel(null)
      if (expandedId === id) setExpandedId(null)
    })

  const skip = useCallback(() => {
    if (!current) return
    setSkipped((s) => new Set(s).add(current.id))
    setSession((s) => ({ ...s, skipped: s.skipped + 1 }))
  }, [current])

  const exitQueue = () => {
    setView('table')
    setIdx(0)
    setSkipped(new Set())
    setSession({ approved: 0, rejected: 0, skipped: 0 })
  }

  const rewatchSkipped = () => {
    setSkipped(new Set())
    setIdx(0)
    setSession({ approved: 0, rejected: 0, skipped: 0 })
  }

  // 队列键盘：A 定审 / S 跳过 / X 否决 / Esc 退出（输入框聚焦时忽略）
  useEffect(() => {
    if (view !== 'queue') return
    const onKey = (e: KeyboardEvent) => {
      const tag = (document.activeElement?.tagName || '').toUpperCase()
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (!current) {
        if (e.key === 'Escape') exitQueue()
        return
      }
      if (e.key === 'a' || e.key === 'A') decide(current.id, 'approve')
      else if (e.key === 'x' || e.key === 'X') decide(current.id, 'reject')
      else if (e.key === 's' || e.key === 'S') skip()
      else if (e.key === 'Escape') exitQueue()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [view, current, decide, skip])

  const toggleSelect = (id: number) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const batchReview = (action: 'approve' | 'reject') =>
    guard(async () => {
      for (const id of selected) {
        await api(`/topics/${id}/${action}`, { method: 'POST' })
      }
      await reload()
      setSelected(new Set())
    })

  if (!booted) return null

  /* ================= 审阅队列视图 ================= */
  if (view === 'queue') {
    const cur = current ? merged(current.id)! : null
    return (
      <div className="mx-auto max-w-3xl">
        <div className="mb-4 flex items-center gap-3">
          <span className="text-sm font-semibold whitespace-nowrap text-slate-700">
            审阅 {doneCount + (current ? 1 : 0)} / {totalAtStart}
          </span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
            <div
              className="h-full rounded-full bg-sky-500 transition-all"
              style={{ width: `${totalAtStart ? (doneCount / totalAtStart) * 100 : 0}%` }}
            />
          </div>
          <button onClick={exitQueue} className="btn-ghost btn-xs whitespace-nowrap">
            退出审阅
          </button>
        </div>

        {!cur ? (
          /* 本轮审完 */
          <div className="card p-10 text-center">
            <div className="text-lg font-semibold text-slate-900">本轮审完 🎉</div>
            <div className="mt-2 text-sm text-slate-500">
              定审 <b className="text-emerald-600">{session.approved}</b> 条 · 否决{' '}
              <b className="text-red-500">{session.rejected}</b> 条 · 跳过{' '}
              <b className="text-slate-600">{session.skipped}</b> 条
            </div>
            {session.approved > 0 && (
              <div className="mt-3 text-xs text-slate-400">定审的选题可到「脚本工场」一键生成三版口播稿。</div>
            )}
            <div className="mt-6 flex justify-center gap-2">
              {session.skipped > 0 && drafts.some((t) => skipped.has(t.id)) && (
                <button onClick={rewatchSkipped} className="btn-ghost">
                  <RotateCcw size={14} /> 重看跳过的 {session.skipped} 条
                </button>
              )}
              <button onClick={exitQueue} className="btn-primary">
                回到选题库
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="card space-y-4 p-6">
              <div className="flex items-start justify-between gap-4">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={`badge ${BADGE_SOURCE[cur.source_type] || BADGE_SOURCE.manual}`}>
                    {SOURCE_LABELS[cur.source_type] || cur.source_type}
                  </span>
                  <span className={`badge ${BADGE_AUDIENCE_LABELS[cur.audience] || 'bg-slate-100 text-slate-500'}`}>
                    {AUDIENCE_LABELS[cur.audience] || cur.audience}
                  </span>
                  {isNew(cur.created_at) && <span className="badge bg-sky-500 text-white">NEW</span>}
                  {cur.created_at && (
                    <span className="badge bg-slate-50 text-slate-400">
                      <Clock size={10} /> {relTime(cur.created_at)}
                    </span>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  <div className={`text-3xl leading-none font-bold tabular-nums ${scoreColor(cur.score)}`}>
                    {cur.score != null ? cur.score.toFixed(1) : '—'}
                  </div>
                  <div className="mt-1 text-[10px] text-slate-400">选题分</div>
                </div>
              </div>

              <div className="text-xl leading-snug font-bold text-slate-900">{cur.title}</div>
              {cur.angle && <div className="text-sm leading-relaxed text-slate-500">{cur.angle}</div>}

              <div className="border-t border-slate-100 pt-4">
                {cur.evidence?.hooks?.length || cur.evidence?.reason || cur.research_report || cur.evidence?.search ? (
                  <DetailBody t={cur} />
                ) : (
                  <div className="py-6 text-center text-sm text-slate-400">暂无深研内容（等待任务完成或来源未深研）</div>
                )}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-[1.2fr_0.8fr_1.2fr] gap-3">
              <button onClick={() => decide(cur.id, 'approve')} className="btn-success justify-center py-3 text-base">
                <Check size={17} /> 定审
              </button>
              <button onClick={skip} className="btn-ghost justify-center border-slate-300 py-3 text-base">
                <SkipForward size={16} /> 跳过
              </button>
              <button onClick={() => decide(cur.id, 'reject')} className="btn-danger justify-center py-3 text-base">
                <X size={17} /> 否决
              </button>
            </div>
            <div className="mt-3 text-center text-xs text-slate-400">快捷键：A 定审 · S 跳过 · X 否决 · Esc 退出</div>
          </>
        )}
        {busy && <div className="mt-3"><Busy text={busy} /></div>}
        {error && <div className="mt-3"><ErrorLine text={error} /></div>}
      </div>
    )
  }

  /* ================= 选题库表格视图 ================= */
  return (
    <div>
      <PageHeader
        icon={Lightbulb}
        title="选题库"
        desc="定审后的选题进入脚本工场投产"
        actions={
          <div className="flex items-center gap-2">
            {drafts.length > 0 && (
              <button onClick={() => { setView('queue'); setIdx(0); setSkipped(new Set()); setSession({ approved: 0, rejected: 0, skipped: 0 }) }} className="btn-accent">
                <Play size={14} /> 审阅 {drafts.length} 条待审
              </button>
            )}
            <span className="text-xs text-slate-400">共 {total} 条</span>
          </div>
        }
      />

      {/* idea 入口 */}
      <div className="card border-sky-200/70 bg-gradient-to-br from-sky-50/80 to-white p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Lightbulb size={15} className="absolute top-1/2 left-3 -translate-y-1/2 text-sky-500" />
            <input
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitIdea()}
              placeholder="一句话 idea，回车开始深研（例：中小企业上 AI 质检，第一步该做什么）"
              className="input border-sky-200 pl-9"
            />
          </div>
          <button onClick={submitIdea} disabled={!idea.trim()} className="btn-accent justify-center disabled:opacity-40 sm:w-36">
            <Lightbulb size={15} /> idea 深研
          </button>
        </div>
        <div className="mt-2 text-[11px] text-slate-400">
          提交后自动：联网检索 → 深研报告 → 打分（约 1~2 分钟），列表行内实时可见进度
        </div>
      </div>


      {/* 状态 chips + 来源 + 批量 */}
      <div className="mt-4 flex flex-wrap items-center gap-1.5">
        {['', 'draft', 'approved', 'rejected', 'produced'].map((s) => (
          <button
            key={s || 'all'}
            onClick={() => { setStatus(s); setOffset(0) }}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${
              status === s
                ? 'bg-slate-900 text-sky-400 shadow-sm'
                : 'bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700'
            }`}
          >
            {s ? STATUS_LABELS[s] : '全部'} {statusCount(s)}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-1.5">
          <div className="segment">
            <button onClick={() => { setOrder('new'); setOffset(0) }} className={`segment-item ${order === 'new' ? 'segment-item-active' : ''}`}>最新入库</button>
            <button onClick={() => { setOrder('score'); setOffset(0) }} className={`segment-item ${order === 'score' ? 'segment-item-active' : ''}`}>分数最高</button>
          </div>
          <select value={audience} onChange={(e) => { setAudience(e.target.value); setOffset(0) }} className="select text-xs">
            <option value="">全部受众</option>
            {Object.entries(AUDIENCE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <select value={source} onChange={(e) => { setSource(e.target.value); setOffset(0) }} className="select text-xs">
            <option value="">全部来源</option>
            {Object.entries(SOURCE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          {(status === 'rejected' || (counts['rejected'] || 0) > 0) && (
            confirmClean ? (
              <button onClick={async () => { const r = await api<{ deleted: number }>('/topics/cleanup-rejected', { method: 'POST', body: JSON.stringify({ days: 30 }) }); setConfirmClean(false); setBusy(`已清理 ${r.deleted} 条 30 天前的已否决选题`); await reload() }} className="btn-xs btn-danger">
                确认清 30 天前已否决
              </button>
            ) : (
              <button onClick={() => { setConfirmClean(true); setTimeout(() => setConfirmClean(false), 4000) }} className="btn-xs btn-ghost text-slate-500">
                清理已否决
              </button>
            )
          )}
          <button
            onClick={() => {
              setBatchMode((v) => !v)
              setSelected(new Set())
            }}
            className={`btn-xs ${batchMode ? 'btn-primary' : 'btn-ghost'}`}
          >
            {batchMode ? <CheckSquare size={13} /> : <ListChecks size={13} />}
            {batchMode ? '退出批量' : '批量'}
          </button>
        </div>
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {/* 紧凑表格 */}
      <div className="card mt-4 overflow-hidden p-0">
        {filtered.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Inbox}
              title="暂无选题"
              desc="从上方一句话 idea 开始深研；或在「拆解库」页扫描对标账号自动产出选题候选。"
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((t) => {
              const exp = expandedId === t.id
              const full = exp ? merged(t.id) : null
              return (
                <div key={t.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => {
                      if (batchMode) toggleSelect(t.id)
                      else setExpandedId(exp ? null : t.id)
                    }}
                    className={`flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50 ${
                      batchMode && selected.has(t.id) ? 'bg-sky-50' : ''
                    }`}
                  >
                    {batchMode && (
                      <span className="shrink-0 text-sky-600">
                        {selected.has(t.id) ? <CheckSquare size={16} /> : <Square size={16} className="text-slate-300" />}
                      </span>
                    )}
                    <span className={`w-10 shrink-0 text-right text-base font-bold tabular-nums ${scoreColor(t.score)}`}>
                      {t.score != null ? t.score.toFixed(1) : '—'}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        {isNew(t.created_at) && <span className="badge bg-sky-500 text-white">NEW</span>}
                        <span className="truncate text-sm font-medium text-slate-900">{t.title}</span>
                      </div>
                      {t.angle && <div className="mt-0.5 truncate text-xs text-slate-400">{t.angle}</div>}
                    </div>
                    <span className={`badge hidden shrink-0 sm:inline-flex ${BADGE_SOURCE[t.source_type] || BADGE_SOURCE.manual}`}>
                      {SOURCE_LABELS[t.source_type] || t.source_type}
                    </span>
                    <span className={`badge shrink-0 ${BADGE_STATUS[t.status] || BADGE_STATUS.draft}`}>
                      {STATUS_LABELS[t.status] || t.status}
                    </span>
                    {t.status === 'draft' && resProg[t.id] && (
                      <span className="hidden w-44 shrink-0 text-xs text-sky-600 md:inline">
                        深研中 {resProg[t.id].p}% {resProg[t.id].m}
                      </span>
                    )}
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      {relTime(t.created_at)}
                    </span>
                    {!batchMode && (
                      <span className="flex shrink-0 items-center gap-1">
                        {t.status === 'draft' && (
                          <>
                            <button
                              onClick={(e) => { e.stopPropagation(); decide(t.id, 'approve') }}
                              title="定审"
                              className="btn-ghost btn-xs text-emerald-600 hover:bg-emerald-50"
                            >
                              <Check size={14} />
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); decide(t.id, 'reject') }}
                              title="否决"
                              className="btn-ghost btn-xs text-red-500 hover:bg-red-50"
                            >
                              <X size={14} />
                            </button>
                          </>
                        )}
                        {confirmDel === t.id ? (
                          <button
                            onClick={(e) => { e.stopPropagation(); deleteTopic(t.id) }}
                            className="btn-danger btn-xs"
                          >
                            确认删除
                          </button>
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              setConfirmDel(t.id)
                              setTimeout(() => setConfirmDel((c) => (c === t.id ? null : c)), 3000)
                            }}
                            title="删除选题"
                            className="btn-ghost btn-xs text-slate-300 hover:text-red-500"
                          >
                            <Trash2 size={13} />
                          </button>
                        )}
                        <ChevronDown size={14} className={`text-slate-300 transition-transform ${exp ? 'rotate-180' : ''}`} />
                      </span>
                    )}
                  </div>

                  {exp && full && (
                    <div className="space-y-4 border-t border-sky-100 px-5 py-4">
                      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.7fr_1fr]">
                        {/* 左栏：深研内容 */}
                        <div>
                          <DetailBody t={full} showHooks={false} />
                          {!(full.evidence?.search || full.evidence?.reason || full.research_report || (full.evidence?.hooks?.length > 0)) && (
                            <div className="py-6 text-center text-sm text-slate-400">暂无深研内容（等待任务完成或来源未深研）</div>
                          )}
                        </div>
                        {/* 右栏：元信息 + 钩子候选 + 下游联动 */}
                        <div className="space-y-3">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className={`badge ${BADGE_STATUS[full.status] || BADGE_STATUS.draft}`}>
                              {STATUS_LABELS[full.status] || full.status}
                            </span>
                            <span className={`badge ${BADGE_SOURCE[full.source_type] || BADGE_SOURCE.manual}`}>
                              {SOURCE_LABELS[full.source_type] || full.source_type}
                            </span>
                            <span className="badge bg-slate-100 text-slate-600">{AUDIENCE_LABELS[full.audience] || full.audience}</span>
                            {full.created_at && (
                              <span className="badge bg-slate-50 text-slate-400"><Clock size={10} /> {relTime(full.created_at)}</span>
                            )}
                          </div>
                          {full.evidence?.hooks?.length > 0 && (
                            <div>
                              <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
                                <Sparkles size={12} className="text-sky-500" /> 钩子候选（点击复制）
                              </div>
                              <ul className="space-y-1.5">
                                {full.evidence.hooks.map((h: string, i: number) => (
                                  <li
                                    key={i}
                                    onClick={() => { navigator.clipboard.writeText(h); setCopiedHook(h); setTimeout(() => setCopiedHook((c) => (c === h ? null : c)), 2000) }}
                                    className="flex cursor-pointer items-start gap-2 rounded-lg border-l-2 border-sky-400 bg-sky-50/60 px-3 py-1.5 text-[13px] leading-relaxed text-slate-700 transition hover:bg-sky-100/70"
                                    title="点击复制"
                                  >
                                    <span className="min-w-0 flex-1">{h}</span>
                                    {copiedHook === h && <span className="shrink-0 text-[10px] font-medium text-emerald-600">已复制</span>}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                          <div className="rounded-lg bg-slate-50 px-3 py-2.5 text-xs space-y-1.5">
                            <div className="font-semibold text-slate-500">下游产出</div>
                            {full.scripts?.length ? (
                              <div className="text-slate-600">
                                脚本：{full.scripts.map((x) => `#${x.id}（${x.status === 'final' ? '已定稿' : x.status === 'failed' ? '失败' : '待定稿'}）`).join('、')}
                              </div>
                            ) : full.status === 'approved' ? (
                              <div className="text-sky-600">已定审 → 到「脚本工场」生成三版脚本</div>
                            ) : (
                              <div className="text-slate-400">暂无</div>
                            )}
                            {(full.articles?.length || 0) > 0 && (
                              <div className="text-slate-600">长文：{full.articles!.map((x) => `#${x.id}`).join('、')}</div>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex gap-2 pt-1">
                        {full.status === 'draft' ? (
                          <>
                            <button onClick={() => decide(full.id, 'approve')} className="btn-success">
                              <Check size={14} /> 定审
                            </button>
                            <button onClick={() => decide(full.id, 'reject')} className="btn-danger">
                              <X size={14} /> 否决
                            </button>
                          </>
                        ) : full.status === 'rejected' ? (
                          <button onClick={() => decide(full.id, 'approve')} className="btn-success">
                            <Check size={14} /> 恢复定审
                          </button>
                        ) : (
                          <button onClick={() => decide(full.id, 'reject')} className="btn-danger">
                            <X size={14} /> 撤销并否决
                          </button>
                        )}
                        {full.status === 'approved' && (
                          <span className="self-center text-xs text-emerald-700">下一步：到「脚本工场」生成三版口播稿</span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          <Pager total={total} limit={LIMIT} offset={offset} onPage={setOffset} />

          </div>
        )}
      </div>

      {/* 批量操作条 */}
      {batchMode && selected.size > 0 && (
        <div className="fixed inset-x-0 bottom-5 z-50 flex justify-center px-4">
          <div className="flex items-center gap-3 rounded-2xl bg-slate-900 px-5 py-3 text-sm text-slate-100 shadow-xl">
            <span className="tabular-nums">
              已选 <b className="text-sky-400">{selected.size}</b> 条
            </span>
            <button onClick={() => batchReview('approve')} className="btn-success btn-xs">
              <Check size={13} /> 批量定审
            </button>
            <button onClick={() => batchReview('reject')} className="btn-danger btn-xs">
              <X size={13} /> 批量否决
            </button>
            <button onClick={() => setSelected(new Set())} className="text-xs text-slate-400 hover:text-slate-200">
              清除
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const BADGE_AUDIENCE_LABELS: Record<string, string> = {
  boss: 'bg-slate-800 text-slate-100',
  fde: 'bg-slate-100 text-slate-700',
  both: 'bg-slate-100 text-slate-500',
}
