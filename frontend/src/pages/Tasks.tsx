import { useCallback, useEffect, useState } from 'react'
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
  parked: number
  queued: number
  superseded: number
  failed: number
  succeeded: number
}

const TYPE_NAMES: Record<string, string> = {
  idea_research: '选题深研',
  benchmark_analyze: '同行拆解',
  script_generate: '脚本生成',
  script_polish: '脚本打磨',
  article_generate: '长文生成',
  avatar_video: '数字人视频',
  script_finalize: '脚本定稿',
  watch_scan: '同行扫描',
  self_scan: '自我扫描',
  self_analyze: '风格拆解',
  self_profile_update: '画像重算',
  watch_resolve: '链接识别',
  watch_discover: '账号发现',
  topic_radar: '话题雷达',
}

const STATUS_BADGE: Record<string, string> = {
  running: 'bg-sky-100 text-sky-700',
  queued: 'bg-slate-200 text-slate-600',
  parked: 'bg-violet-100 text-violet-700',
  superseded: 'bg-slate-100 text-slate-400',
  failed: 'bg-red-100 text-red-700',
  succeeded: 'bg-emerald-100 text-emerald-700',
}
const STATUS_LABEL: Record<string, string> = {
  running: '运行中', queued: '排队', parked: '额度挂起', superseded: '已续跑', failed: '失败', succeeded: '完成',
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

export default function Tasks({ onNavigate }: { onNavigate?: (k: SectionKey) => void }) {
  const [items, setItems] = useState<TaskRow[]>([])
  const [counts, setCounts] = useState<Counts>({ running: 0, queued: 0, parked: 0, superseded: 0, failed: 0, succeeded: 0 })
  const [total, setTotal] = useState(0)
  const [filter, setFilter] = useState('')
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
  const [schedOpen, setSchedOpen] = useState(false)
  const [editHour, setEditHour] = useState<string | null>(null)

  const reloadScheds = useCallback(async () => {
    const r = await api<{ items: ScheduleRow[] }>('/schedules')
    setScheds(r.items)
  }, [])

  // 待处置：失败任务按错误指纹聚类（任务中台），修复根因后一键整批复活
  const reloadDisposal = useCallback(async () => {
    const r = await api<{ items: FailureGroup[] }>('/jobs/failures')
    setDisposal(r.items)
  }, [])

  const reload = useCallback(async (f: string, pg: number) => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(pg * PAGE_SIZE) })
    if (f) params.set('status', f)
    const r = await api<{ items: TaskRow[]; total: number; counts: Counts }>(`/jobs?${params}`)
    setItems(r.items)
    setTotal(r.total)
    setCounts(r.counts)
  }, [filter, page])

  useEffect(() => {
    reload(filter, page).catch((e) => setError(String(e)))
    reloadDisposal().catch(() => {})
    reloadScheds().catch(() => {})
  }, [reload, filter, page, tick, reloadDisposal, reloadScheds])

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

  const changeFilter = (k: string) => {
    setFilter(k)
    setPage(0)
  }

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
        await Promise.all([reload(filter, page), reloadDisposal()])
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
        await Promise.all([reloadScheds(), reload(filter, page)])
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
        await reload(filter, page)
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
        await reload(filter, page)
      } catch (e) {
        setError(String(e))
      }
    })()

  const retryOne = (id: number) =>
    (async () => {
      setError('')
      try {
        await api(`/jobs/${id}/retry`, { method: 'POST' })
        await reload(filter, page)
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
        await reload(filter, page)
      } catch (e) {
        setError(String(e))
      }
    })()

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const filtered = items
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

  return (
    <div>
      <PageHeader
        icon={Activity}
        title="任务"
        desc="扫描、拆解、生成等全部后台任务与实时进展"
        actions={
          <div className="flex items-center gap-2">
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
            <button onClick={() => setAuto((v) => !v)} className={auto ? 'btn-ghost' : 'btn-ghost text-slate-400'}>
              {auto ? <PauseCircle size={14} /> : <PlayCircle size={14} />} 自动刷新：{auto ? '开' : '关'}
            </button>
          </div>
        }
      />

      {/* 状态行 */}
      <div className="card flex flex-wrap items-center gap-x-5 gap-y-1 px-5 py-3 text-[13px] text-slate-600">
        <span>运行中 <b className="text-sky-600">{counts.running}</b></span>
        <span>排队 <b className="text-zinc-900">{counts.queued}</b></span>
        {counts.parked > 0 && <span className="text-violet-500">额度挂起 <b>{counts.parked}</b></span>}
        <span>失败 <b className="text-red-500">{counts.failed}</b></span>
        <span>已完成 <b className="text-zinc-900">{counts.succeeded}</b></span>
        {counts.superseded > 0 && <span className="text-slate-400">已续跑 {counts.superseded}</span>}
        <span className="ml-auto text-[11px] text-slate-400">
          {auto ? `每 3 秒自动刷新 · 最近更新 ${lastUpdate}` : '自动刷新已暂停'}
        </span>
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {/* 调度中心：所有周期任务一处可见可管（节奏/上次/下次/开关/立即执行） */}
      <div className="card mt-3 overflow-hidden p-0">
        <button
          onClick={() => setSchedOpen((v) => !v)}
          className="flex w-full flex-wrap items-center gap-2 px-4 py-2.5 text-sm font-semibold text-zinc-900"
        >
          <Clock size={14} className="text-sky-600" /> 调度中心
          <span className="text-xs font-normal text-slate-400">
            {scheds.filter((s) => s.enabled).length}/{scheds.length} 个周期任务启用中 · 点开管理节奏与开关
          </span>
          <span className="ml-auto text-xs text-slate-400">{schedOpen ? '收起 ▲' : '展开 ▼'}</span>
        </button>
        {schedOpen && (
          <div className="divide-y divide-slate-100 border-t border-slate-100">
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
        )}
      </div>

      {/* 待处置（任务中台）：非空的失败都带着原因聚类在这里，不许死得无声无息 */}
      {disposal.length > 0 && (
        <div className="card mt-3 overflow-hidden border-amber-200 bg-amber-50/40 p-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-amber-200/70 px-4 py-2.5 text-sm font-semibold text-amber-900">
            <CircleAlert size={14} /> 待处置 {disposal.reduce((n, g) => n + g.count, 0)} 条 · {disposal.length} 类原因
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
        </div>
      )}

      <div className="mt-4">
        <ChipRow
          active={filter}
          onChange={changeFilter}
          chips={[
            { key: '', label: '全部', count: counts.running + counts.queued + counts.parked + counts.failed + counts.succeeded },
            { key: 'running', label: '运行中', count: counts.running },
            { key: 'queued', label: '排队', count: counts.queued },
            { key: 'parked', label: '额度挂起', count: counts.parked },
            { key: 'failed', label: '失败', count: counts.failed },
            { key: 'succeeded', label: '已完成', count: counts.succeeded },
            { key: 'superseded', label: '已续跑', count: counts.superseded },
          ]}
        />
      </div>

      <div className="card mt-4 overflow-hidden p-0">
        {filtered.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Activity}
              title="没有符合筛选的任务"
              desc="流水线各环节产生的任务会自动出现在这里。"
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((t) => {
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
                    <span className={`badge shrink-0 ${STATUS_BADGE[t.status] || 'bg-slate-200 text-slate-600'}`}>
                      {STATUS_LABEL[t.status] || t.status}
                    </span>
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
                        {t.status === 'parked' && t.next_retry_at && (
                          <span className="ml-1.5 text-xs text-violet-500">
                            {relTime(t.next_retry_at + 'Z')}自动重查额度
                          </span>
                        )}
                        {t.status === 'failed' && t.next_retry_at && (
                          <span className="ml-1.5 text-xs text-amber-500">
                            {relTime(t.next_retry_at + 'Z')}自动重试（第 {(t.retry_count || 0)} 次）
                          </span>
                        )}
                        {t.status === 'succeeded' && (
                          <span className="ml-1.5 text-xs text-slate-400">
                            {t.message || (t.payload?.benchmark_id ? '完成' : '')}
                          </span>
                        )}
                      </div>
                      {(t.error || (isRun && t.message)) && (
                        <div className={`mt-0.5 truncate text-xs ${t.error ? 'text-red-500' : 'text-slate-400'}`}>
                          {t.fail_class && t.status === 'failed' && (
                            <span className="mr-1.5 rounded bg-red-50 px-1 py-px text-[10px] text-red-400">
                              {FAIL_CLASS_LABEL[t.fail_class] || t.fail_class}
                            </span>
                          )}
                          {t.error || t.message}
                        </div>
                      )}
                    </div>
                    <span className="shrink-0 text-[11px] text-slate-400">{duration(t)}</span>
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      {relTime(t.created_at)}
                    </span>
                    {t.status === 'failed' && (
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
                        <div className="space-y-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`badge ${STATUS_BADGE[t.status] || 'bg-slate-200 text-slate-600'}`}>
                              {STATUS_LABEL[t.status] || t.status}
                            </span>
                            <span className="text-sm font-semibold text-zinc-900">
                              {TYPE_NAMES[t.type] || t.type} · 任务 #{t.id}
                            </span>
                            {(() => {
                              const links: [SectionKey, string][] = []
                              if (t.payload?.benchmark_id != null) links.push(['benchmarks', '查看拆解样本 →'])
                              if (t.payload?.script_id != null) links.push(['scripts', '查看脚本 →'])
                              if (t.payload?.article_id != null) links.push(['articles', '查看长文 →'])
                              if (t.payload?.topic_id != null) links.push(['topics', '查看选题 →'])
                              return links.map(([k, label]) => (
                                <button key={k} onClick={() => onNavigate?.(k)} className="text-xs text-sky-600 hover:underline">
                                  {label}
                                </button>
                              ))
                            })()}
                          </div>
                        </div>
                      ) : null}
                      {detail && detail.id === t.id ? (
                        <div className="grid gap-4 md:grid-cols-2">
                          <div className="space-y-3">
                            <div>
                              <div className="mb-1 text-xs font-semibold text-slate-500">输入参数</div>
                              <pre className="overflow-auto rounded-lg bg-slate-50 p-3 text-[12px] leading-relaxed text-slate-700">
                                {JSON.stringify(t.payload, null, 2)}
                              </pre>
                            </div>
                            {(t.lane || t.fail_class || t.retry_count) && (
                              <div>
                                <div className="mb-1 text-xs font-semibold text-slate-500">调度信息</div>
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
                              <button
                                onClick={() => setTick((x) => x + 1)}
                                className="mt-1.5 text-sky-600 hover:underline"
                              >
                                刷新此任务 →
                              </button>
                            </div>
                            {(detail.history?.length ?? 0) > 0 && (
                              <div>
                                <div className="mb-1.5 text-xs font-semibold text-slate-500">阶段轨迹</div>
                                <ol className="space-y-1">
                                  {detail.history!.map((h, i) => (
                                    <li key={i} className="flex items-center gap-2 text-[12px] text-slate-600">
                                      <span className="w-9 shrink-0 text-right tabular-nums text-slate-400">{h.p}%</span>
                                      <span className="size-1.5 shrink-0 rounded-full bg-sky-400" />
                                      <span className="truncate">{h.m}</span>
                                      <span className="ml-auto shrink-0 text-[10px] text-slate-300">
                                        {h.t ? new Date(h.t).toLocaleTimeString() : ''}
                                      </span>
                                    </li>
                                  ))}
                                </ol>
                              </div>
                            )}
                          </div>
                          <div className="space-y-3">
                            {t.error && (
                              <div>
                                <div className="mb-1 text-xs font-semibold text-slate-500">完整错误</div>
                                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-red-200 bg-red-50/60 p-3 text-[12px] leading-relaxed text-red-700">
                                  {t.error}
                                </pre>
                              </div>
                            )}
                            {detail.result && Object.keys(detail.result).length > 0 && (
                              <div>
                                <div className="mb-1 text-xs font-semibold text-slate-500">执行结果</div>
                                {Array.isArray(detail.result.notes) && detail.result.notes.length > 0 ? (
                                  <ul className="space-y-1 text-[12px] leading-relaxed text-slate-700">
                                    {detail.result.notes.map((n: string, i: number) => (
                                      <li key={i}>· {n}</li>
                                    ))}
                                  </ul>
                                ) : (
                                  <pre className="max-h-48 overflow-auto rounded-lg bg-slate-50 p-3 text-[12px] leading-relaxed text-slate-700">
                                    {JSON.stringify(detail.result, null, 2)}
                                  </pre>
                                )}
                              </div>
                            )}
                            {!t.error && !(detail.result && Object.keys(detail.result).length > 0) && (
                              <div className="text-xs text-slate-400">{t.message || '暂无结果'}</div>
                            )}
                          </div>
                        </div>
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

      {total > PAGE_SIZE && (
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
      )}
    </div>
  )
}
