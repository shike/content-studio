import { useCallback, useEffect, useState } from 'react'
import { BarChart3, RefreshCw } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, PageHeader, Spark } from '../components'

/**
 * 数据（PRD R6）：经营看板——总览 / 渠道对比 / 账号趋势 / 作品表现 / 周报。
 * 只读事实表；缺数显式「未回流」，不补零；不做素材归因（R6.6）。
 */

interface Overview {
  days: number
  accounts: {
    fans: number; new_fans: number; cancel_fans: number; profile_views: number
    play: number; leads: number; fans_current: number; fans_as_of: string | null
    days_with_data: number
  }
  contents_new: number
  published_total: number
  published_recent: number
  last_publish_at: string | null
}
interface ChannelRow {
  key: string; name: string; platform: string; enabled: boolean
  contents_new: number; published: number; accounts: number; fans_total: number | null
}
interface AccountTrend {
  id: number; name: string; platform: string; enabled: boolean; days: number
  latest: { date: string; fans: number; new_fans: number; profile_views: number; play: number } | null
  series: { date: string; fans: number; new_fans: number; profile_views: number; play: number }[]
}
interface PubRow {
  content_id: number | null; title: string; kind: string; platform: string
  url: string; published_at: string; mode: string
  metrics: Record<string, number>; backfilled: boolean
}

/* ── 自账号 30 天全指标卡 + 视频表现周报（2026-11-17 自人设号·经营迁入，用户裁决：
数据归数据菜单；经营 tab 只留风格画像与自账号管理）── */

interface MetricsRow {
  date: string
  fans: number
  new_fans: number
  cancel_fans: number
  profile_views: number
  play: number
  digg: number
  comment: number
  share: number
  account_search: number
  post_search: number
}

interface MetricsSummary {
  days: number
}

const METRICS: { key: keyof MetricsRow; label: string; stock: boolean }[] = [
  { key: "fans", label: "粉丝总量", stock: true },
  { key: "new_fans", label: "净增粉丝", stock: false },
  { key: "cancel_fans", label: "取关", stock: false },
  { key: "profile_views", label: "主页访问", stock: false },
  { key: "play", label: "播放", stock: false },
  { key: "digg", label: "点赞", stock: false },
  { key: "comment", label: "评论", stock: false },
  { key: "share", label: "转发", stock: false },
  { key: "account_search", label: "账号搜索", stock: false },
  { key: "post_search", label: "作品搜索", stock: false },
]

function AccountMetricsCard() {
  const [m, setM] = useState<{ items: MetricsRow[]; summary: MetricsSummary } | null>(null)
  const [loadErr, setLoadErr] = useState("")
  useEffect(() => {
    api<{ items: MetricsRow[]; summary: MetricsSummary }>("/metrics/account?days=30")
      .then(setM)
      .catch((e) => setLoadErr(String(e).replace(/^Error:\s*/, "")))
  }, [])
  if (loadErr) {
    return (
      <div className="card mt-4 px-4 py-3 text-[13px] text-red-600">
        账号数据加载失败：{loadErr}
      </div>
    )
  }
  if (!m || m.items.length === 0) {
    return (
      <div className="card mt-4 px-4 py-3 text-[13px] text-slate-400">
        还没有回流数据（每晚自动从创作者中心回流）
      </div>
    )
  }
  const week = m.items.slice(-7)
  const prevWeek = m.items.slice(-14, -7)
  return (
    <div className="card mt-4 p-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-zinc-800">账号数据（近 30 天）</div>
        <span className="text-[11px] text-slate-400">{m.summary.days} 天</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {METRICS.map(({ key, label, stock }) => {
          const vals = m.items.map((r) => (r[key] as number) ?? 0)
          const latest = vals[vals.length - 1] ?? 0
          const sum7 = week.reduce((a, r) => a + ((r[key] as number) ?? 0), 0)
          const sumPrev = prevWeek.reduce((a, r) => a + ((r[key] as number) ?? 0), 0)
          const delta = prevWeek.length
            ? Math.round(((sum7 - sumPrev) / Math.max(sumPrev, 1)) * 100)
            : null
          return (
            <div key={key} className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2">
              <div className="text-[11px] text-slate-400">{label}</div>
              <div className="num-serif text-lg font-semibold text-zinc-900">
                {stock ? Number(latest).toLocaleString() : Number(sum7).toLocaleString()}
              </div>
              <div className={`text-[10px] ${delta != null && delta > 0 ? "text-emerald-600" : delta != null && delta < 0 ? "text-red-500" : "text-slate-300"}`}>
                {delta != null && label !== "粉丝总量" ? `较上一周 ${delta > 0 ? "+" : ""}${delta}%` : "\u00A0"}
              </div>
              <Spark data={vals} wide />
            </div>
          )
        })}
      </div>
    </div>
  )
}

function WeeklyReportCard() {
  const [rep, setRep] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState("")
  const load = useCallback(() => {
    api<Record<string, unknown>>("/metrics/weekly").then(setRep).catch((e) => setError(String(e).replace(/^Error:\s*/, "")))
  }, [])
  useEffect(load, [load])
  const videos = (rep?.videos as { title: string; play: number; score: number }[] | undefined) || []
  const top = videos.slice(0, 3)
  const cards = (rep?.cards as { hook: string; video: string; play: number; comment: number; score: number }[] | undefined) || []
  const cardsUnmatched = Number(rep?.cards_unmatched ?? 0)
  return (
    <div className="card mt-4 p-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-zinc-800">视频表现周报{rep?.week ? `（${String(rep.week)}）` : ""}</div>
      </div>
      {error && <div className="mt-2 text-xs text-red-600">{error}</div>}
      {!rep?.week && !error && (
        <div className="mt-2 text-xs text-slate-400">还没有周报（每周末自动生成）</div>
      )}
      {top.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {top.map((v, i) => (
            <div key={i} className="flex items-center gap-2 text-[13px]">
              <span className="badge shrink-0 bg-emerald-50 text-emerald-600">TOP{i + 1}</span>
              <span className="min-w-0 flex-1 truncate text-zinc-700">{v.title}</span>
              <span className="num-serif shrink-0 text-slate-500">播 {v.play} · 分 {v.score}</span>
            </div>
          ))}
        </div>
      )}
      {cards.length > 0 && (
        <div className="mt-3 border-t border-zinc-100 pt-3">
          <div className="text-xs font-medium text-zinc-500">靶子卡 → 视频表现（校准回路：哪类钩子真的成立，看这里）</div>
          <div className="mt-1.5 space-y-1.5">
            {cards.map((x, i) => (
              <div key={i} className="text-[12.5px] leading-snug">
                <span className="font-medium text-zinc-800">{x.hook}</span>
                <span className="text-slate-300"> → </span>
                <span className="text-zinc-600">{x.video}</span>
                <span className="num-serif ml-1 text-slate-500">播 {x.play} · 评 {x.comment}</span>
              </div>
            ))}
            {cardsUnmatched > 0 && (
              <div className="text-[11px] text-slate-400">另有 {cardsUnmatched} 张备稿卡未匹配到已发视频（标题对不上或还没拍）</div>
            )}
          </div>
        </div>
      )}
      {Boolean(rep?.summary) && <SummaryText text={String(rep?.summary ?? "")} />}
    </div>
  )
}

/** 周报摘要轻量渲染：LLM 输出 **加粗**、空行分段、- 列表——原样塞 div 会被挤成
一坨无结构纯文本（2026-11-07 用户指认格式混乱）。只处理这三样，别写通用 markdown。 */
function SummaryText({ text }: { text: string }) {
  const lines = text.split("\n")
  return (
    <div className="mt-3 rounded-xl bg-sky-50/70 px-4 py-3 text-[13px] leading-relaxed text-zinc-700 space-y-1.5">
      {lines.map((line, i) => {
        const trimmed = line.trim()
        if (!trimmed) return null
        const isBullet = /^[-•]\s/.test(trimmed)
        const content = isBullet ? trimmed.replace(/^[-•]\s/, "") : trimmed
        const parts = content.split(/(\*\*[^*]+\*\*)/g).map((seg, j) =>
          seg.startsWith("**") && seg.endsWith("**") && seg.length > 4
            ? <strong key={j} className="font-semibold text-zinc-900">{seg.slice(2, -2)}</strong>
            : <span key={j}>{seg}</span>)
        return isBullet
          ? <div key={i} className="flex gap-1.5"><span className="shrink-0 text-sky-500">•</span><span className="min-w-0">{parts}</span></div>
          : <p key={i}>{parts}</p>
      })}
    </div>
  )
}
interface Weekly {
  week: string; message?: string; summary?: string
  median_play?: number; pub_day_avg_play?: number; idle_day_avg_play?: number
  fans_delta_week?: number; profile_views_week?: number
  videos?: { title: string; play: number; score: number; published_at: string }[]
  daily?: { date: string; play: number; new_fans: number; profile_views: number }[]
}

const num = (v: number | null | undefined) =>
  v == null ? '—' : v >= 10000 ? `${(v / 10000).toFixed(1)}万` : String(v)

function Tab({ tabs, tab, onTab }: { tabs: { id: string; label: string }[]; tab: string; onTab: (t: string) => void }) {
  return (
    <div className="mb-5 flex items-center gap-1.5 border-b border-[#E8ECF0] pb-2.5">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onTab(t.id)}
          className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${tab === t.id ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

function OverviewTab({ days }: { days: number }) {
  const [ov, setOv] = useState<Overview | null>(null)
  const [weekly, setWeekly] = useState<Weekly | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    Promise.all([
      api<Overview>(`/analytics/overview?days=${days}`),
      api<Weekly>('/analytics/weekly'),
    ]).then(([o, w]) => { setOv(o); setWeekly(w) })
      .catch((e) => setError(apiErrText(e)))
  }, [days])
  useEffect(() => { load() }, [load])

  const recompute = async () => {
    setError(''); setBusy(true)
    try {
      const r = await api<{ job_id: number }>('/analytics/weekly/recompute', { method: 'POST' })
      await waitJob(r.job_id, (j) => setError(`周报重算：${j.progress}% ${j.message || ''}`))
      load()
    } catch (e) { setError(`周报重算失败：${apiErrText(e)}`) } finally { setBusy(false) }
  }

  if (!ov) { return <ErrorLine text={error} /> }
  const a = ov.accounts
  const big = [
    { label: '当前粉丝', v: num(a.fans_current), sub: a.fans_as_of ? `截至 ${a.fans_as_of}` : '未回流' },
    { label: `${days} 天净增`, v: num(a.new_fans), sub: `取关 ${num(a.cancel_fans)}` },
    { label: `${days} 天主页访问`, v: num(a.profile_views), sub: '课程转化北极星' },
    { label: `${days} 天播放`, v: num(a.play), sub: `回流 ${a.days_with_data} 天` },
  ]
  return (
    <div>
      <ErrorLine text={error} />
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {big.map((b) => (
          <div key={b.label} className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
            <div className="text-[11px] tracking-wide text-slate-400">{b.label}</div>
            <div className="mt-1 text-xl font-semibold text-slate-900 num-serif">{b.v}</div>
            <div className="mt-0.5 text-[11px] text-slate-400">{b.sub}</div>
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
          <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">内容产出（近 {days} 天）</div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-slate-600">
            <span>新建稿件 <b className="num-serif text-slate-900">{ov.contents_new}</b></span>
            <span>发布 <b className="num-serif text-slate-900">{ov.published_recent}</b>（累计 {ov.published_total}）</span>
            <span className="text-slate-400">{ov.last_publish_at ? `最近发布 ${ov.last_publish_at}` : '还没有发布登记'}</span>
          </div>
        </div>
        <div className="rounded-xl border border-[#d3d6ec] bg-linear-to-br from-white to-[#f4f5fa] px-4 py-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[12px] font-medium tracking-wide text-slate-500">周报{weekly?.week ? ` · ${weekly.week}` : ''}</span>
            <button onClick={recompute} disabled={busy} className="btn-ghost btn-xs">
              <RefreshCw size={12} className="inline" /> 重新生成
            </button>
          </div>
          {weekly?.message ? (
            <div className="text-[12.5px] text-slate-400">{weekly.message}</div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-slate-600">
                <span>中位播放 <b className="num-serif text-slate-900">{num(weekly?.median_play)}</b></span>
                <span>发布日均 <b className="num-serif text-slate-900">{num(weekly?.pub_day_avg_play)}</b> vs
                  停更日均 <b className="num-serif text-slate-900">{num(weekly?.idle_day_avg_play)}</b></span>
                <span>周净增 <b className="num-serif text-slate-900">{num(weekly?.fans_delta_week)}</b></span>
              </div>
              {weekly?.summary && (
                <p className="mt-2 text-[12.5px] leading-relaxed text-slate-600">{weekly.summary}</p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ChannelsTab({ days }: { days: number }) {
  const [rows, setRows] = useState<ChannelRow[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    api<{ items: ChannelRow[] }>(`/analytics/channels?days=${days}`)
      .then((d) => setRows(d.items || []))
      .catch((e) => setError(apiErrText(e)))
  }, [days])
  return (
    <div>
      <ErrorLine text={error} />
      {rows.length === 0 ? <EmptyState icon={BarChart3} title="没有渠道数据" desc="渠道注册表为空（启动播种异常）" /> : (
        <div className="card overflow-hidden p-0">
          <div className="grid grid-cols-6 gap-2 border-b border-slate-100 bg-slate-50 px-4 py-2 text-[11px] font-semibold tracking-wide text-slate-400">
            <span className="col-span-2">渠道</span><span>近{days}天新建</span><span>发布</span><span>账号</span><span>粉丝</span>
          </div>
          <div className="divide-y divide-slate-100">
            {rows.map((r) => (
              <div key={r.key} className="grid grid-cols-6 gap-2 px-4 py-2.5 text-[13px] text-slate-600">
                <span className="col-span-2 font-medium text-slate-900">
                  {r.name}{!r.enabled && <span className="ml-1.5 text-[11px] text-slate-400">停用</span>}
                  <span className="ml-1.5 text-[11px] text-slate-400">{r.platform}</span>
                </span>
                <span className="num-serif">{r.contents_new}</span>
                <span className="num-serif">{r.published}</span>
                <span className="num-serif">{r.accounts || '—'}</span>
                <span className="num-serif">{r.fans_total == null ? <span className="text-slate-400">未回流</span> : num(r.fans_total)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function AccountsTab({ days }: { days: number }) {
  const [rows, setRows] = useState<AccountTrend[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    api<{ items: AccountTrend[] }>(`/analytics/accounts?days=${days}`)
      .then((d) => setRows(d.items || []))
      .catch((e) => setError(apiErrText(e)))
  }, [days])
  return (
    <div>
      <ErrorLine text={error} />
      <AccountMetricsCard />
      {rows.length === 0 ? <EmptyState icon={BarChart3} title="还没有自有账号" desc="在「人设号 · 经营」页登记" /> : (
        <div className="space-y-2">
          {rows.map((a) => (
            <div key={a.id} className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-[14px] font-semibold text-slate-900">{a.name}</span>
                <span className={`rounded-full px-2 py-0.5 text-[11px] ${a.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                  {a.enabled ? '监控中' : '停用'}
                </span>
                {a.latest ? (
                  <>
                    <span className="text-[12.5px] text-slate-600">粉丝 <b className="num-serif text-slate-900">{num(a.latest.fans)}</b></span>
                    <span className="text-[12px] text-slate-400">近 {a.days} 天有数据</span>
                    <span className="ml-auto"><Spark data={a.series.map((x) => x.fans)} wide /></span>
                  </>
                ) : (
                  <span className="text-[12.5px] text-slate-400">近 {days} 天未回流</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PublicationsTab({ days }: { days: number }) {
  const [rows, setRows] = useState<PubRow[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    api<{ items: PubRow[] }>(`/analytics/publications?limit=50`)
      .then((d) => setRows(d.items || []))
      .catch((e) => setError(apiErrText(e)))
  }, [days])
  const KIND: Record<string, string> = { long_article: '长文', video_script: '视频稿', note: '笔记', douyin_video: '抖音作品' }
  return (
    <div>
      <ErrorLine text={error} />
      <WeeklyReportCard />
      {rows.length === 0 ? <EmptyState icon={BarChart3} title="还没有发布记录" desc="各渠道发布后登记链接，或等自有账号数据回流" /> : (
        <div className="space-y-2">
          {rows.map((r, ix) => (
            <div key={ix} className="flex flex-wrap items-center gap-2 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
              <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">{KIND[r.kind] || r.kind}</span>
              <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-slate-900">{r.title || '（无标题）'}</span>
              {Object.entries(r.metrics).map(([k, v]) => (
                <span key={k} className="shrink-0 text-[11.5px] text-slate-500">{k} <b className="num-serif text-slate-800">{num(v)}</b></span>
              ))}
              {Object.keys(r.metrics).length === 0 && <span className="shrink-0 text-[11.5px] text-slate-400">指标未回流</span>}
              <span className="shrink-0 text-[11px] text-slate-400">{(r.published_at || '').slice(0, 10)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Analytics() {
  const [tab, setTab] = useState('overview')
  const [days, setDays] = useState(30)
  const tabs = [
    { id: 'overview', label: '总览' },
    { id: 'channels', label: '渠道对比' },
    { id: 'accounts', label: '账号趋势' },
    { id: 'pubs', label: '作品表现' },
  ]
  return (
    <div>
      <PageHeader icon={BarChart3} title="数据"
        desc="四渠道经营事实表：账号指标每日自动回流（创作者中心），作品指标发布后补录；缺数显式标「未回流」，不补零。" />
      <Tab tabs={tabs} tab={tab} onTab={setTab} />
      {tab === 'overview' && <OverviewTab days={days} />}
      {tab === 'channels' && <ChannelsTab days={days} />}
      {tab === 'accounts' && <AccountsTab days={days} />}
      {tab === 'pubs' && <PublicationsTab days={days} />}
    </div>
  )
}
