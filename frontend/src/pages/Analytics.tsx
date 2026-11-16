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
