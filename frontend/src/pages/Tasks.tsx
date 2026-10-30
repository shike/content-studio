import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, ChevronLeft, ChevronRight, CircleAlert, Clock, Eraser, PauseCircle, PlayCircle, RefreshCw, Trash2 } from 'lucide-react'
import type { SectionKey } from '../App'
import { api } from '../api'
import { Busy, ChipRow, EmptyState, ErrorLine, PageHeader, relTime } from '../components'

interface TaskRow {
  id: number
  type: string
  status: string
  progress: number
  message: string
  error: string | null
  payload: Record<string, any>
  created_at?: string
  updated_at?: string
  queue_position?: number
  dedup_key?: string
  lane?: string
  retry_count?: number
  max_retries?: number
  next_retry_at?: string | null
  fail_class?: string
  covered_by?: number
  retry_of?: number
}

const LANE_LABEL: Record<string, string> = { heavy: 'LLM 串行', light: '轻并发', external: '外部渲染', scheduled: '定时' }
const FAIL_CLASS_LABEL: Record<string, string> = {
  transient: '瞬时故障·自动重试', quota: '额度窗口', captcha: '风控·可人工重试', deterministic: '确定性失败',
}

interface Counts {
  running: number
  parked: number  // 已退役状态（历史行兼容）
  queued: number
  superseded: number
  failed: number
  succeeded: number
  summary: { active: number; todo: number; done: number }
}

const TYPE_NAMES: Record<string, string> = {
  idea_research: '选题深研',
  benchmark_analyze: '同行拆解',
  script_generate: '脚本生成',
  script_polish: '脚本打磨',
  article_generate: '长文生成',
  script_finalize: '脚本定稿',
  watch_scan: '同行扫描',
  self_scan: '自我扫描',
  self_analyze: '风格拆解',
  self_profile_update: '画像重算',
  watch_resolve: '链接识别',
  watch_discover: '账号发现',
  topic_radar: '话题雷达',
}

// 用户可见三态（2026-10-12 引擎统一）：failed+next_retry_at=退避中（属"进行中"）
type UserState = 'active' | 'todo' | 'done'
function userState(t: { status: string; next_retry_at?: string | null }): UserState {
  if (t.status === 'succeeded' || t.status === 'superseded') return 'done'
  if (t.status === 'failed') return t.next_retry_at ? 'active' : 'todo'
  return 'active'
}
const STATE_BADGE: Record<UserState, string> = {
  active: 'bg-sky-100 text-sky-700',
  todo: 'bg-red-100 text-red-700',
  done: 'bg-emerald-100 text-emerald-700',
}
function stateBadge(t: { status: string; next_retry_at?: string | null; retry_count?: number }): { cls: string; label: string } {
  const st = userState(t)
  if (t.status === 'running') return { cls: STATE_BADGE.active, label: '运行中' }
  if (t.status === 'queued') return { cls: STATE_BADGE.active, label: '排队' }
  if (t.status === 'superseded') return { cls: STATE_BADGE.done, label: '已续跑' }
  if (t.status === 'succeeded') return { cls: STATE_BADGE.done, label: '完成' }
  if (st === 'active') return { cls: STATE_BADGE.active, label: `重试中 ${t.retry_count || 0}/4` }
  return { cls: STATE_BADGE.todo, label: '需要处理' }
}

interface ScheduleRow {
  key: string
  label: string
  hour: number | null
  interval_hours: number | null
  daily: boolean
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  last_status: string
  run_count: number
}

interface FailureGroup {
  fp: string
  kind: 'job' | 'entity'
  count: number
  label: string
  root_cause: string
  pattern_status: string
  sample_error: string
  sample_job_id: number
  last_type: string
  fail_class: string
  orphans?: { entity_id: number; title: string }[]
}

const PAGE_SIZE = 50

function objectLabel(payload: Record<string, any>): string {
  for (const [key, prefix] of [
    ['benchmark_id', '拆解'], ['script_id', '脚本'], ['article_id', '长文'],
    ['video_id', '视频'], ['topic_id', '选题'], ['account_id', '账号'],
  ] as const) {
    if (payload?.[key] != null) return `${prefix} #${payload[key]}`
  }
  if (payload?.url) return '链接识别'
  return ''
}

type Tab = 'run' | 'disposal' | 'sched' | 'history'

export default function Tasks({ onNavigate }: { onNavigate?: (k: SectionKey) => void }) {
  const [items, setItems] = useState<TaskRow[]>([])
  const [counts, setCounts] = useState<Counts>({
    running: 0, parked: 0, queued: 0, superseded: 0, failed: 0, succeeded: 0,
    summary: { active: 0, todo: 0, done: 0 },
  })
  const [total, setTotal] = useState(0)
  const [tab, setTab] = useState<Tab>('run')
  const tabTouched = useRef(false)
  const [runFilter, setRunFilter] = useState('active')
  const [page, setPage] = useState(0)
  const [auto, setAuto] = useState(true)
  const [tick, setTick] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<(TaskRow & { result?: Record<string, any>; history?: { p: number; m: string; t: string }[] }) | null>(null)
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [confirmClear, setConfirmClear] = useState<string | null>(null)
  const [clearMenu, setClearMenu] = useState(false)
  const [disposal, setDisposal] = useState<FailureGroup[]>([])
  const [expFp, setExpFp] = useState<string | null>(null)
  const [scheds, setScheds] = useState<ScheduleRow[]>([])
  const [editHour, setEditHour] = useState<string | null>(null)

  // 页面按"来这想问什么"分四个标签：运行总览 / 待处置 / 调度中心 / 历史记录（2026-10-30 重构）
  // 落位规则：待处置非空且用户没点过标签 → 自动停在待处置
  const switchTab = (k: Tab) => { tabTouched.current = true; setTab(k); setPage(0); setExpandedId(null) }
  const disposalTotal = disposal.reduce((n, g) => n + g.count, 0)
  useEffect(() => {
    if (!tabTouched.current && disposal.length > 0) setTab('disposal')
  }, [disposal])

  // 查询口径随标签走：历史记录固定 done，其余标签由运行总览的 chips 决定
  const effF = tab === 'history' ? 'done' : runFilter

  const reloadScheds = useCallback(async () => {
    const r = await api<{ items: ScheduleRow[] }>('/schedules')
    setScheds(r.items)
  }, [])

  // 待处置：失败任务按错误指纹聚类（任务中台），修复根因后一键整批复活
  const reloadDisposal = useCallback(async () => {
    const r = await api<{ items: FailureGroup[] }>('/jobs/failures')
    setDisposal(r.items)
  }, [])

  const reload = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
    if (effF) params.set('status', effF)
    const r = await api<{ items: TaskRow[]; total: number; counts: Counts }>(`/jobs?${params}`)
    setItems(r.items)
    setTotal(r.total)
    setCounts(r.counts)
  }, [effF, page])

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    reloadDisposal().catch(() => {})
    reloadScheds().catch(() => {})
  }, [reload, tick, reloadDisposal, reloadScheds])

  // 实时刷新：3s 轻量轮询，可暂停
  useEffect(() => {
    if (!auto) return
    const t = setInterval(() => setTick((x) => x + 1), 3000)
    return () => clearInterval(t)
  }, [auto])

  // 展开的任务：拉全量详情（含 result），auto 开启时跟随刷新
  useEffect(() => {
    if (expandedId == null) {
      setDetail(null)
      return
    }
    api<TaskRow & { result?: Record<string, any> }>(`/jobs/${expandedId}`)
      .then(setDetail)
      .catch(() => {})
  }, [expandedId, tick])

  const changeRunFilter = (k: string) => { setRunFilter(k); setPage(0) }

  const retryFp = (fp: string) =>
    (async () => {
      setError('')
      try {
        const r = await api<{ requeued: number }>('/jobs/retry-by-fingerprint', {
          method: 'POST',
          body: JSON.stringify({ fp }),
        })
        setBusy(`已按指纹复活 ${r.requeued} 条任务（实体已同步回在途态）`)
        setTimeout(() => setBusy(''), 3000)
        await Promise.all([reload(), reloadDisposal()])
      } catch (e) {
        setError(String(e))
      }
    })()

  const resolveFp = (fp: string, status: 'fixed' | 'ignored') =>
    (async () => {
      setError('')
      try {
        await api(`/jobs/failures/${encodeURIComponent(fp)}/resolve`, {
          method: 'POST',
          body: JSON.stringify({ status }),
        })
        setBusy(status === 'fixed' ? '已标记已修复（该组移出待处置）' : '已忽略该类失败')
        setTimeout(() => setBusy(''), 3000)
        await reloadDisposal()
      } catch (e) {
        setError(String(e))
      }
    })()

  const schedAction = (key: string, action: 'toggle' | 'run', hour?: number) =>
    (async () => {
      setError('')
      try {
        if (action === 'toggle') {
          await api(`/schedules/${key}/toggle`, { method: 'POST' })
        } else if (action === 'run') {
          const r = await api<{ message: string }>(`/schedules/${key}/run`, { method: 'POST' })
          setBusy(`${key}：${r.message}`)
          setTimeout(() => setBusy(''), 3000)
        }
        await Promise.all([reloadScheds(), reload()])
      } catch (e) {
        setError(String(e))
      }
    })()

  const saveHour = (key: string) =>
    (async () => {
      const h = Number(editHour)
      if (Number.isNaN(h) || h < 0 || h > 23) { setEditHour(null); return }
      setError('')
      try {
        await api(`/schedules/${key}/hour`, { method: 'PUT', body: JSON.stringify({ hour: h }) })
        setEditHour(null)
        await reloadScheds()
      } catch (e) {
        setError(String(e))
      }
    })()

  const removeTask = (id: number) =>
    (async () => {
      setError('')
      setConfirmDel(null)
      try {
        await api(`/jobs/${id}`, { method: 'DELETE' })
        if (expandedId === id) setExpandedId(null)
        await reload()
      } catch (e) {
        setError(String(e))
      }
    })()

  const retryAllFailed = () =>
    (async () => {
      setError('')
      setConfirmClear(null)
      try {
        const r = await api<{ requeued: number }>('/jobs/retry-failed', {
          method: 'POST',
          body: JSON.stringify({}),
        })
        setBusy(`已重新入队 ${r.requeued} 个失败任务`)
        setTimeout(() => setBusy(''), 3000)
        await Promise.all([reload(), reloadDisposal()])
      } catch (e) {
        setError(String(e))
      }
    })()

  const retryOne = (id: number) =>
    (async () => {
      setError('')
      try {
        await api(`/jobs/${id}/retry`, { method: 'POST' })
        await reload()
      } catch (e) {
        setError(String(e))
      }
    })()

  const clearStatus = (st: string) =>
    (async () => {
      setError('')
      setConfirmClear(null)
      try {
        const r = await api<{ archived: number }>('/jobs/clear', {
          method: 'POST',
          body: JSON.stringify({ status: st }),
        })
        setBusy(`已归档 ${r.archived} 条${st === 'failed' ? '失败' : st === 'superseded' ? '已续跑' : '已完成'}记录`)
        setTimeout(() => setBusy(''), 3000)
        await reload()
        await reloadDisposal()
      } catch (e) {
        setError(String(e))
      }
    })()

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const lastUpdate = new Date().toLocaleTimeString()

  const duration = (row: TaskRow) => {
    const start = row.created_at ? new Date(row.created_at + 'Z').getTime() : 0
    if (!start) return ''
    const end = row.status === 'running' || row.status === 'queued'
      ? Date.now()
      : (row.updated_at ? new Date(row.updated_at + 'Z').getTime() : Date.now())
    const sec = Math.max(0, Math.round((end - start) / 1000))
    return sec >= 60 ? `${Math.floor(sec / 60)}分${sec % 60}秒` : `${sec}秒`
  }

  const pagination = total > PAGE_SIZE && (
    <div className="mt-3 flex items-center justify-center gap-3 text-[13px] text-slate-500">
      <button
        onClick={() => setPage((x) => Math.max(0, x - 1))}
        disabled={page === 0}
        className="btn-ghost btn-xs disabled:opacity-40"
      >
        <ChevronLeft size={13} /> 上一页
      </button>
      <span>第 {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} 条 · 共 {total} 条</span>
      <button
        onClick={() => setPage((x) => Math.min(pages - 1, x + 1))}
        disabled={page >= pages - 1}
        className="btn-ghost btn-xs disabled:opacity-40"
      >
        下一页 <ChevronRight size={13} />
      </button>
    </div>
  )

  const tabs: [Tab, string, number | null][] = [
    ['run', '运行总览', null],
    ['disposal', '待处置', disposalTotal > 0 ? disposalTotal : null],
    ['sched', '调度中心', null],
    ['history', '历史记录', null],
  ]

  return (
    <div>
      <PageHeader
        icon={Activity}
        title="任务"
        desc="扫描、拆解、生成等全部后台任务与实时进展"
        actions={
          <button onClick={() => setAuto((v) => !v)} className={auto ? 'btn-ghost' : 'btn-ghost text-slate-400'}>
            {auto ? <PauseCircle size={14} /> : <PlayCircle size={14} />} 自动刷新：{auto ? '开' : '关'}
          </button>
        }
      />

      {/* 状态行 */}
      <div className="card flex flex-wrap items-center gap-x-5 gap-y-1 px-5 py-3 text-[13px] text-slate-600">
        <span>进行中 <b className="text-sky-600">{counts.summary.active}</b></span>
        <span className="text-xs text-slate-400">（运行中 {counts.running} · 排队 {counts.queued} · 等重试 {counts.failed && counts.summary.active - counts.running - counts.queued >= 0 ? counts.summary.active - counts.running - counts.queued : 0}）</span>
        <span>需要处理 <b className={counts.summary.todo ? 'text-red-500' : 'text-zinc-900'}>{counts.summary.todo}</b></span>
        <span>完成 <b className="text-zinc-900">{counts.summary.done}</b></span>
        <span className="ml-auto text-[11px] text-slate-400">
          {auto ? `每 3 秒自动刷新 · 最近更新 ${lastUpdate}` : '自动刷新已暂停'}
        </span>
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {/* 标签导航 */}
      <div className="mb-3 mt-4 flex gap-1.5">
        {tabs.map(([k, label, n]) => (
          <button key={k} onClick={() => switchTab(k)}
            className={`flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
            {n != null && (
              <span className={`rounded-full px-1.5 text-[11px] font-semibold ${tab === k ? 'bg-white/20 text-white' : 'bg-red-100 text-red-600'}`}>{n}</span>
            )}
          </button>
        ))}
      </div>

      {/* ── Tab① 运行总览 ── */}
      {tab === 'run' && (
        <>
          <div className="mb-3">
            <ChipRow
              active={runFilter}
              onChange={changeRunFilter}
              chips={[
                { key: 'active', label: '进行中', count: counts.summary.active },
                { key: 'todo', label: '需要处理', count: counts.summary.todo },
              ]}
            />
          </div>
          <div className="card overflow-hidden p-0">
            {items.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={Activity}
                  title={runFilter === 'active' ? '当前没有进行中的任务' : '没有需要处理的失败任务'}
                  desc="流水线各环节产生的任务会自动出现在这里；历史记录在「历史记录」标签。"
                />
              </div>
            ) : (
              <div className="divide-y divide-slate-100">
                {items.map((t) => {
                  const obj = objectLabel(t.payload)
                  const isRun = t.status === 'running'
                  const isQueued = t.status === 'queued'
                  const showBar = isRun || isQueued
                  const exp = expandedId === t.id
                  return (
                    <div key={t.id} className={exp ? 'bg-sky-50/40' : ''}>
                      <div
                        onClick={() => setExpandedId(exp ? null : t.id)}
                        className="cursor-pointer px-4 pt-2.5 pb-2 transition hover:bg-slate-50"
                      >
                        <div className="flex items-center gap-3">
                          {(() => {
                            const b = stateBadge(t)
                            return <span className={`badge shrink-0 ${b.cls}`}>{b.label}</span>
                          })()}
                          {t.status === 'failed' && t.covered_by != null && (
                            <span className="badge shrink-0 bg-emerald-100 text-emerald-700" title={`后续任务 #${t.covered_by} 已成功重跑同一工作，此失败已闭环`}>
                              ✓ 已闭环
                            </span>
                          )}
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm text-zinc-900">
                              <span className="font-medium">{TYPE_NAMES[t.type] || t.type}</span>
                              {obj && <span className="ml-1.5 text-xs text-slate-400">{obj}</span>}
                              {isQueued && t.queue_position != null && (
                                <span className="ml-1.5 text-xs text-slate-400">队列第 {t.queue_position} 位</span>
                              )}
                              {t.lane && (
                                <span className="ml-1.5 rounded bg-slate-100 px-1 py-px text-[10px] text-slate-400">
                                  {LANE_LABEL[t.lane] || t.lane}
                                </span>
                              )}
                              {t.status === 'failed' && t.next_retry_at && (
                                <span className="ml-1.5 text-xs text-sky-600">
                                  第 {(t.retry_count || 0)}/4 次重试 · {relTime(t.next_retry_at + 'Z')}自动重试
                                </span>
                              )}
                              {t.status === 'succeeded' && (
                                <span className="ml-1.5 text-xs text-slate-400">
                                  {t.message || (t.payload?.benchmark_id ? '完成' : '')}
                                </span>
                              )}
                            </div>
                            {userState(t) === 'active' && t.status === 'failed' ? (
                              <div className="mt-0.5 truncate text-xs text-slate-400">
                                <span className="mr-1.5 rounded bg-sky-50 px-1 py-px text-[10px] text-sky-600">
                                  上次失败（退避中，非当前失败）
                                </span>
                                {t.error}
                              </div>
                            ) : userState(t) === 'todo' ? (
                              <div className="mt-0.5 truncate text-xs text-red-500">
                                {t.fail_class && (
                                  <span className="mr-1.5 rounded bg-red-50 px-1 py-px text-[10px] text-red-400">
                                    {FAIL_CLASS_LABEL[t.fail_class] || t.fail_class}
                                  </span>
                                )}
                                {t.error || t.message}
                              </div>
                            ) : (
                              <div className="mt-0.5 truncate text-xs text-slate-400">
                                {t.error || (isRun && t.message)}
                              </div>
                            )}
                          </div>
                          <span className="shrink-0 text-[11px] text-slate-400">{duration(t)}</span>
                          <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                            {relTime(t.created_at)}
                          </span>
                          {userState(t) === 'todo' && (
                            <button
                              onClick={(e) => { e.stopPropagation(); retryOne(t.id) }}
                              title="重新入队执行"
                              className="btn-ghost btn-xs shrink-0 text-sky-600"
                            >
                              <RefreshCw size={12} /> 重试
                            </button>
                          )}
                          {t.status !== 'running' && (
                            confirmDel === t.id ? (
                              <button
                                onClick={(e) => { e.stopPropagation(); removeTask(t.id) }}
                                className="btn-danger btn-xs shrink-0"
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
                                title="删除此任务记录"
                                className="shrink-0 text-slate-300 hover:text-red-500"
                              >
                                <Trash2 size={13} />
                              </button>
                            )
                          )}
                        </div>
                        {showBar && (
                          <div className="mt-1.5 flex items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                              <div
                                className={`h-full rounded-full transition-all duration-500 ${isRun ? 'bg-sky-600' : 'bg-slate-300'}`}
                                style={{ width: `${t.progress}%` }}
                              />
                            </div>
                            <span className="w-14 shrink-0 text-right text-[11px] tabular-nums text-slate-400">
                              {t.progress}%
                            </span>
                          </div>
                        )}
                      </div>

                      {exp && (
                        <div className="border-t border-sky-100 px-5 py-4">
                          {detail && detail.id === t.id ? (
                            <>
                              {/* 详情头：联动入口 + 刷新（状态/类型列表行上已有，不重复） */}
                              <div className="mb-3 flex flex-wrap items-center gap-2">
                                <span className="text-sm font-semibold text-zinc-900">任务 #{t.id}</span>
                                {(() => {
                                  const links: [SectionKey, string][] = []
                                  if (t.payload?.benchmark_id != null) links.push(['benchmarks', '查看拆解样本 →'])
                                  if (t.payload?.script_id != null) links.push(['scripts', '查看脚本 →'])
                                  if (t.payload?.article_id != null) links.push(['articles', '查看长文 →'])
                                  if (t.payload?.topic_id != null) links.push(['topics', '查看选题 →'])
                                  return links.map(([k, label]) => (
                                    <button key={k} onClick={() => onNavigate?.(k)}
                                      className="rounded-full bg-sky-50 px-2.5 py-0.5 text-xs text-sky-700 hover:bg-sky-100">
                                      {label}
                                    </button>
                                  ))
                                })()}
                                <button onClick={() => setTick((x) => x + 1)}
                                  className="ml-auto inline-flex items-center gap-1 text-xs text-sky-600 hover:underline">
                                  <RefreshCw size={12} /> 刷新此任务
                                </button>
                              </div>
                              <div className="grid gap-4 md:grid-cols-5">
                                {/* 左栏·主体：结论（第一视口）→ 阶段轨迹 */}
                                <div className="space-y-4 md:col-span-3">
                                  <div>
                                    <div className="mb-1 text-xs font-semibold text-slate-500">
                                      {t.error ? '完整错误' : '执行结果'}
                                    </div>
                                    {t.error ? (
                                      <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-red-200 bg-red-50/60 p-3 text-[12px] leading-relaxed text-red-700">
                                        {t.error}
                                      </pre>
                                    ) : detail.result && Object.keys(detail.result).length > 0 ? (
                                      Array.isArray(detail.result.notes) && detail.result.notes.length > 0 ? (
                                        <ul className="space-y-1 text-[12px] leading-relaxed text-slate-700">
                                          {detail.result.notes.map((n: string, i: number) => (
                                            <li key={i}>· {n}</li>
                                          ))}
                                        </ul>
                                      ) : (
                                        <pre className="max-h-56 overflow-auto rounded-lg bg-slate-50 p-3 text-[12px] leading-relaxed text-slate-700">
                                          {JSON.stringify(detail.result, null, 2)}
                                        </pre>
                                      )
                                    ) : (
                                      <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-400">
                                        {t.message || '暂无结果'}
                                      </div>
                                    )}
                                  </div>
                                  {(detail.history?.length ?? 0) > 0 && (
                                    <div>
                                      <div className="mb-1.5 text-xs font-semibold text-slate-500">阶段轨迹（最新一步在下）</div>
                                      <ol className="space-y-1">
                                        {detail.history!.map((h, i) => {
                                          const latest = i === (detail.history!.length - 1)
                                          return (
                                            <li key={i} className={`flex items-center gap-2 rounded-md px-1.5 py-0.5 text-[12px] ${latest ? 'bg-sky-50 font-medium text-sky-800' : 'text-slate-600'}`}>
                                              <span className="w-9 shrink-0 text-right tabular-nums text-slate-400">{h.p}%</span>
                                              <span className={`size-1.5 shrink-0 rounded-full ${latest ? 'bg-sky-600' : 'bg-slate-300'}`} />
                                              <span className="truncate">{h.m}</span>
                                              <span className="ml-auto shrink-0 text-[10px] text-slate-300">
                                                {h.t ? new Date(h.t).toLocaleTimeString() : ''}
                                              </span>
                                            </li>
                                          )
                                        })}
                                      </ol>
                                    </div>
                                  )}
                                </div>
                                {/* 右栏·执行档案：输入参数 / 调度 / 时间 */}
                                <div className="space-y-3 md:col-span-2">
                                  <div>
                                    <div className="mb-1 text-xs font-semibold text-slate-500">输入参数</div>
                                    <pre className="max-h-44 overflow-auto rounded-lg bg-slate-50 p-3 text-[12px] leading-relaxed text-slate-700">
                                      {JSON.stringify(t.payload, null, 2)}
                                    </pre>
                                  </div>
                                  {(t.lane || t.fail_class || t.retry_count) && (
                                    <div>
                                      <div className="mb-1 text-xs font-semibold text-slate-500">调度档案</div>
                                      <div className="space-y-1 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                                        <div>分道：{t.lane === 'light' ? '轻并发' : t.lane === 'external' ? '外部渲染' : t.lane === 'scheduled' ? '定时' : 'LLM 串行'}</div>
                                        <div>重试：{t.retry_count || 0} / {t.max_retries || 0} 次</div>
                                        {t.fail_class && <div>失败分类：{t.fail_class === 'transient' ? '瞬时故障' : t.fail_class === 'quota' ? '额度窗口' : t.fail_class === 'captcha' ? '风控拦截' : '确定性失败'}</div>}
                                        {t.retry_of ? <div>血缘：由失败任务 #{t.retry_of} 重试而来</div> : null}
                                        {t.covered_by != null && <div className="text-emerald-600">已闭环：后续任务 #{t.covered_by} 已成功重跑同一工作，此失败无需处理</div>}
                                        {t.next_retry_at && <div>自动重试排期：{new Date(t.next_retry_at + 'Z').toLocaleString()}</div>}
                                        {t.dedup_key && <div className="truncate" title={t.dedup_key}>去重键：{t.dedup_key}</div>}
                                      </div>
                                    </div>
                                  )}
                                  <div className="text-xs text-slate-500">
                                    <div>创建：{t.created_at ? new Date(t.created_at + 'Z').toLocaleString() : '—'}</div>
                                    <div className="mt-0.5">更新：{t.updated_at ? new Date(t.updated_at + 'Z').toLocaleString() : '—'}</div>
                                  </div>
                                </div>
                              </div>
                            </>
                          ) : (
                            <div className="text-xs text-slate-400">加载详情中…</div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          {pagination}
        </>
      )}

      {/* ── Tab② 待处置（失败中台）── */}
      {tab === 'disposal' && (
        <>
          <div className="mb-3 flex items-center justify-end">
            {counts.failed > 0 && (
              confirmClear === 'retry' ? (
                <div className="flex items-center gap-1.5 text-xs text-slate-500">
                  重试全部 {counts.failed} 个失败任务（重新入队）？
                  <button onClick={() => { setConfirmClear(null); retryAllFailed() }} className="btn-danger btn-xs">确认</button>
                  <button onClick={() => setConfirmClear(null)} className="btn-ghost btn-xs">取消</button>
                </div>
              ) : (
                <button onClick={() => setConfirmClear('retry')} className="btn-ghost text-slate-500" title="全部失败任务重新入队">
                  <RefreshCw size={14} /> 重试全部失败
                </button>
              )
            )}
          </div>
          <div className="card overflow-hidden border-amber-200 bg-amber-50/40 p-0">
            {disposal.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={CircleAlert}
                  title="没有待处置的失败"
                  desc="失败任务会按原因聚类在这里：修复根因后「复活」整批重跑，不许静默消失。"
                />
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 border-b border-amber-200/70 px-4 py-2.5 text-sm font-semibold text-amber-900">
                  <CircleAlert size={14} /> 待处置 {disposalTotal} 条 · {disposal.length} 类原因
                  <span className="text-xs font-normal text-amber-700/80">
                    修复根因后「复活」整批重跑；标记已修复/忽略后移出本区
                  </span>
                </div>
                <div className="divide-y divide-amber-200/50">
                  {disposal.map((g) => {
                    const exp = expFp === g.fp
                    return (
                      <div key={g.fp}>
                        <div
                          onClick={() => setExpFp(exp ? null : g.fp)}
                          className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-amber-50/70"
                        >
                          <span className={`badge shrink-0 ${g.kind === 'entity' ? 'bg-violet-100 text-violet-700' : 'bg-amber-100 text-amber-800'}`}>
                            {g.kind === 'entity' ? '实体孤儿' : g.fail_class === 'deterministic' ? '确定性失败' : g.fail_class === 'quota' ? '额度耗尽' : '瞬时失败'}
                          </span>
                          <span className="shrink-0 text-sm font-bold tabular-nums text-amber-700">×{g.count}</span>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium text-zinc-900">{g.label}</div>
                            <div className="mt-0.5 truncate text-xs text-slate-500">{g.sample_error}</div>
                          </div>
                          <span className="hidden shrink-0 text-[11px] text-slate-400 md:inline">{g.last_type}</span>
                        </div>
                        {exp && (
                          <div className="space-y-2.5 border-t border-amber-200/50 px-5 py-3 text-[13px]">
                            <div>
                              <div className="mb-1 text-xs font-semibold text-slate-500">最近错误样本</div>
                              <pre className="max-h-40 overflow-auto rounded-lg bg-white/80 p-3 text-xs leading-relaxed whitespace-pre-wrap text-slate-700">{g.sample_error || '（无错误信息）'}</pre>
                            </div>
                            {g.root_cause && (
                              <div className="text-slate-600"><span className="mr-2 text-xs font-semibold text-slate-500">根因备注</span>{g.root_cause}</div>
                            )}
                            {g.orphans && g.orphans.length > 0 && (
                              <div>
                                <div className="mb-1 text-xs font-semibold text-slate-500">受影响实体</div>
                                <ul className="space-y-0.5 text-slate-600">
                                  {g.orphans.map((o) => (
                                    <li key={o.entity_id}>· {o.title}（#{o.entity_id}）</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            <div className="flex flex-wrap items-center gap-2 pt-1">
                              <button onClick={(e) => { e.stopPropagation(); retryFp(g.fp) }} className="btn-accent btn-xs">
                                <RefreshCw size={12} /> {g.kind === 'entity' ? '重新生成' : '复活这批'}
                              </button>
                              {g.kind === 'job' && (
                                <>
                                  <button onClick={(e) => { e.stopPropagation(); resolveFp(g.fp, 'fixed') }} className="btn-ghost btn-xs text-slate-500">
                                    标记已修复
                                  </button>
                                  <button onClick={(e) => { e.stopPropagation(); resolveFp(g.fp, 'ignored') }} className="btn-ghost btn-xs text-slate-400">
                                    忽略此类
                                  </button>
                                </>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </>
            )}
          </div>
        </>
      )}

      {/* ── Tab③ 调度中心 ── */}
      {tab === 'sched' && (
        <div className="card overflow-hidden p-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-zinc-900">
            <Clock size={14} className="text-sky-600" /> 调度中心
            <span className="text-xs font-normal text-slate-400">
              {scheds.filter((s) => s.enabled).length}/{scheds.length} 个周期任务启用中 · 点钟点可改 · 失败后自动重试
            </span>
          </div>
          <div className="divide-y divide-slate-100">
            {scheds.map((s) => (
              <div key={s.key} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-[13px]">
                <span className={`badge shrink-0 ${s.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>
                  {s.enabled ? '启用' : '停用'}
                </span>
                <span className="w-44 shrink-0 font-medium text-zinc-900">{s.label}</span>
                <span className="w-24 shrink-0 text-slate-500">
                  {s.daily
                    ? (editHour === s.key ? (
                        <input
                          autoFocus
                          value={editHour}
                          onChange={(e) => setEditHour(e.target.value)}
                          onBlur={() => saveHour(s.key)}
                          onKeyDown={(e) => e.key === 'Enter' && saveHour(s.key)}
                          placeholder="0-23"
                          className="input w-14 px-1.5 py-0.5 text-xs"
                        />
                      ) : (
                        <button
                          onClick={(e) => { e.stopPropagation(); setEditHour(s.key) }}
                          className="hover:text-sky-600 hover:underline"
                          title="点击调整每日钟点"
                        >
                          每日 {s.hour ?? 0} 点
                        </button>
                      ))
                    : `每 ${s.interval_hours} 小时`}
                </span>
                <span className="shrink-0 text-slate-500">
                  上次 {s.last_run_at ? relTime(s.last_run_at) : '从未'}
                  {s.last_status && <span className="ml-1 text-xs text-slate-400">({s.last_status.slice(0, 40)})</span>}
                </span>
                <span className="shrink-0 text-slate-500">下次 {s.next_run_at ? relTime(s.next_run_at) : '—'}</span>
                <span className="ml-auto flex shrink-0 items-center gap-1.5">
                  <button
                    onClick={() => schedAction(s.key, 'run')}
                    disabled={busy !== ''}
                    className="btn-ghost btn-xs text-sky-600"
                  >
                    <RefreshCw size={12} /> 立即执行
                  </button>
                  <button
                    onClick={() => schedAction(s.key, 'toggle')}
                    className="btn-ghost btn-xs text-slate-500"
                  >
                    {s.enabled ? '停用' : '启用'}
                  </button>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Tab④ 历史记录 ── */}
      {tab === 'history' && (
        <>
          <div className="mb-3 flex items-center justify-end">
            <div className="relative">
              <button onClick={() => setClearMenu((v) => !v)} className={`btn-ghost ${clearMenu ? 'text-slate-800' : 'text-slate-500'}`}>
                <Eraser size={14} /> 清理记录
              </button>
              {clearMenu && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setClearMenu(false)} />
                  <div className="absolute right-0 z-50 mt-1 w-52 rounded-xl border border-slate-200 bg-white py-1.5 shadow-xl">
                    {(['failed', 'superseded', 'succeeded'] as const).map((st) => {
                      const n = counts[st]
                      if (n <= 0) return null
                      const label = st === 'failed' ? '失败' : st === 'superseded' ? '已续跑' : '已完成'
                      return confirmClear === st ? (
                        <div key={st} className="px-3 py-1.5 text-xs text-slate-600">
                          清空 {n} 条{label}？
                          <button onClick={() => { setClearMenu(false); clearStatus(st) }} className="btn-danger btn-xs ml-1.5">确认</button>
                          <button onClick={() => setConfirmClear(null)} className="ml-1 text-slate-400 hover:text-slate-600">取消</button>
                        </div>
                      ) : (
                        <button key={st} onClick={() => setConfirmClear(st)} className="block w-full px-3 py-1.5 text-left text-xs text-slate-600 hover:bg-slate-50">
                          清理{label}记录（{n}）
                        </button>
                      )
                    })}
                  </div>
                </>
              )}
            </div>
          </div>
          <div className="card overflow-hidden p-0">
            {items.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={Activity}
                  title="暂无历史记录"
                  desc="已完成与已续跑的任务会归档在这里。"
                />
              </div>
            ) : (
              <div className="divide-y divide-slate-100">
                {items.map((t) => {
                  const obj = objectLabel(t.payload)
                  return (
                    <div key={t.id}>
                      <div className="flex items-center gap-3 px-4 pt-2.5 pb-2">
                        {(() => {
                          const b = stateBadge(t)
                          return <span className={`badge shrink-0 ${b.cls}`}>{b.label}</span>
                        })()}
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm text-zinc-900">
                            <span className="font-medium">{TYPE_NAMES[t.type] || t.type}</span>
                            {obj && <span className="ml-1.5 text-xs text-slate-400">{obj}</span>}
                            <span className="ml-1.5 text-xs text-slate-400">{t.message || ''}</span>
                          </div>
                        </div>
                        <span className="shrink-0 text-[11px] text-slate-400">{duration(t)}</span>
                        <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                          {relTime(t.created_at)}
                        </span>
                        {confirmDel === t.id ? (
                          <button
                            onClick={() => removeTask(t.id)}
                            className="btn-danger btn-xs shrink-0"
                          >
                            确认删除
                          </button>
                        ) : (
                          <button
                            onClick={() => {
                              setConfirmDel(t.id)
                              setTimeout(() => setConfirmDel((c) => (c === t.id ? null : c)), 3000)
                            }}
                            title="删除此任务记录"
                            className="shrink-0 text-slate-300 hover:text-red-500"
                          >
                            <Trash2 size={13} />
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          {pagination}
        </>
      )}
    </div>
  )
}
