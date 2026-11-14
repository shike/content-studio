import { useCallback, useEffect, useState } from 'react'
import { TrendingUp } from 'lucide-react'
import { api } from '../api'
import { PageHeader } from '../components'
import StyleSection from './Style'

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

/** 30 天迷你趋势线：纯 SVG，每项指标一条（2026-11-08 用户定版：所有数据加趋势图）。 */
function Spark({ data }: { data: number[] }) {
  const vals = data.filter((v) => v != null)
  if (vals.length < 2) return <div className="mt-2 h-6 text-[11px] text-slate-300">数据不足</div>
  const w = 120, h = 28
  const max = Math.max(...vals)
  const min = Math.min(...vals)
  const span = max - min || 1
  const pts = vals
    .map((v, i) => {
      const x = 2 + (i * (w - 4)) / (vals.length - 1)
      const y = h - 4 - ((v - min) / span) * (h - 8)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(" ")
  const last = pts.split(" ").pop()!.split(",")
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-1.5 h-7 w-full" preserveAspectRatio="none" role="img" aria-label="30 天趋势">
      <polyline points={pts} fill="none" stroke="#0a2f24" strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="2" fill="#0a2f24" />
    </svg>
  )
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
              <Spark data={vals} />
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

/** 账号经营（2026-11-07 与风格画像合并为一页；2026-11-08 用户定版：所有数据带趋势图、
计算全部后台自动无手动入口、去自说自话描述）。 */
export default function AccountTrends({ onNavigate }: { onNavigate?: (k: string) => void }) {
  return (
    <div>
      <PageHeader icon={TrendingUp} title="账号经营" desc="我的账号数据与内容表现" />
      <AccountMetricsCard />
      <WeeklyReportCard />
      <StyleSection onNavigate={onNavigate} />
    </div>
  )
}
