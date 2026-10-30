import { useCallback, useEffect, useState } from 'react'
import { TrendingUp } from 'lucide-react'
import { api, waitJob } from '../api'
import { PageHeader } from '../components'

interface MetricsRow {
  date: string
  fans: number
  new_fans: number
  profile_views: number
  play: number
  digg: number
  comment: number
  share: number
  account_search: number
  post_search: number
}

interface MetricsSummary {
  fans: number
  new_fans_7d: number
  profile_views_7d: number
  play_7d: number
  days: number
}

function AccountMetricsCard() {
  const [m, setM] = useState<{ items: MetricsRow[]; summary: MetricsSummary } | null>(null)
  useEffect(() => {
    api<{ items: MetricsRow[]; summary: MetricsSummary }>('/metrics/account?days=30')
      .then(setM)
      .catch(() => setM(null))
  }, [])
  if (!m || m.items.length === 0) return null
  const s = m.summary
  return (
    <div className="card mt-4 p-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-zinc-800">账号数据（近 {s.days} 天）</div>
        <span className="text-[11px] text-slate-400">每天凌晨自动从创作者中心回流</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ['粉丝总量', s.fans],
          ['近7天净增', s.new_fans_7d],
          ['近7天主页访问', s.profile_views_7d],
          ['近7天播放', s.play_7d],
        ].map(([k, v]) => (
          <div key={k as string} className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2">
            <div className="text-[11px] text-slate-400">{k as string}</div>
            <div className="num-serif text-lg font-semibold text-zinc-900">{Number(v).toLocaleString()}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function WeeklyReportCard() {
  const [rep, setRep] = useState<Record<string, unknown> | null>(null)
  const [msg, setMsg] = useState('')
  const [busy2, setBusy2] = useState(false)
  const load = useCallback(() => {
    api<Record<string, unknown>>('/metrics/weekly').then(setRep).catch(() => setRep(null))
  }, [])
  useEffect(load, [load])
  const gen = async () => {
    setBusy2(true)
    try {
      const r = await api<{ job_id: number }>('/metrics/weekly/generate', { method: 'POST' })
      await waitJob(r.job_id, (j) => setMsg(`生成中 ${j.progress}% ${j.message || ''}`))
      setMsg('')
      load()
    } catch (e) {
      setMsg(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy2(false)
    }
  }
  const videos = (rep?.videos as { title: string; play: number; score: number }[] | undefined) || []
  const top = videos.slice(0, 3)
  return (
    <div className="card mt-4 p-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-zinc-800">视频表现周报{rep?.week ? `（${String(rep.week)}）` : ''}</div>
        <button onClick={gen} disabled={busy2} className="btn-ghost btn-xs disabled:opacity-40">
          {busy2 ? '生成中…' : '生成/刷新周报'}
        </button>
      </div>
      {msg && <div className="mt-2 text-xs text-amber-600">{msg}</div>}
      {!rep?.week && !msg && (
        <div className="mt-2 text-xs text-slate-400">还没有周报。数据每天凌晨自动回流，点上面按钮生成上一周的。</div>
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
      {Boolean(rep?.summary) && (
        <div className="mt-3 rounded-xl bg-sky-50/70 px-3 py-2 text-[13px] leading-relaxed text-zinc-700">
          {String(rep?.summary ?? '')}
        </div>
      )}
    </div>
  )
}

/** 账号经营（原「我的账号趋势」拆分，2026-10-30）：账号数据回流 + 视频表现周报。 */
export default function AccountTrends() {
  return (
    <div>
      <PageHeader
        icon={TrendingUp}
        title="账号经营"
        desc="账号数据每日凌晨自动回流 · 视频表现周报"
      />
      <AccountMetricsCard />
      <WeeklyReportCard />
    </div>
  )
}
