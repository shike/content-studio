import { useEffect, useState } from 'react'
import {
  Activity,
  FileText,
  Fingerprint,
  Lightbulb,
  PenLine,
  Radar,
  Scissors,
  Send,
  Zap,
  ArrowRight,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { SectionKey } from '../App'
import { PageHeader } from '../components'
import { fetchMenuStats, fetchTodoStats } from '../counts'
import { api } from '../api'

interface Todo {
  key: SectionKey
  icon: LucideIcon
  label: string
  count: number
  hint: string
}

function QuickTile({
  icon: Icon, label, hint, active, onClick,
}: {
  icon: LucideIcon
  label: string
  hint: string
  active?: boolean
  onClick?: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`card card-hover p-3 text-left ${active ? 'border-sky-300 bg-sky-50/60' : ''}`}
    >
      <div className="flex items-center gap-2">
        <Icon size={14} className="text-sky-600" />
        <span className="text-[13px] font-medium text-zinc-900">{label}</span>
      </div>
      <div className="mt-1.5 truncate text-[11px] text-slate-400">{hint}</div>
    </button>
  )
}

function _fmtW(n: number): string {
  return n >= 10000 ? `${(n / 10000).toFixed(1).replace(/\.0$/, '')}万` : String(n)
}

export default function Dashboard({ onNavigate }: { onNavigate: (k: SectionKey) => void }) {
  const [crawl, setCrawl] = useState<{ status: string; label: string; at: string; recent_failures: number } | null>(null)
  const [digest, setDigest] = useState<{
    new_videos: number; pending: number; topics_7d: number;
    pending_authors: [string, number][]; top_topics: { topic_id: number; title: string; score: number; angle: string }[];
    discovered: { count: number; top: { name: string; follower_count: number; signature: string }[] };
  } | null>(null)
  const [todos, setTodos] = useState<Todo[] | null>(null)
  const [extra, setExtra] = useState({ watch: 0, style: 0, benchmarksPending: 0, benchmarksTodo: 0, publishing: 0, tasks: 0 })
  const [showQuick, setShowQuick] = useState(false)
  const [quickTitle, setQuickTitle] = useState('')
  const [quickAudience, setQuickAudience] = useState('both')
  const [quickMsg, setQuickMsg] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api<{ new_videos: number; pending: number; topics_7d: number;
          pending_authors: [string, number][]; top_topics: { topic_id: number; title: string; score: number; angle: string }[];
          discovered: { count: number; top: { name: string; follower_count: number; signature: string }[] } }>('/watch/digest')
      .then(setDigest)
      .catch(() => setDigest(null))
    api<{ last: { status: string; label?: string; at: string } | null; label: string; recent_failures: number }>('/crawl/status')
      .then((c) => setCrawl({ status: c.last?.status ?? 'never', label: c.label, at: c.last?.at ?? '', recent_failures: c.recent_failures }))
      .catch(() => setCrawl(null))
    fetchTodoStats()
      .then((s) => {
        setTodos([
          { key: 'topics', icon: Lightbulb, label: '待审选题', count: s.draftTopics, hint: '深研/拆解产出的候选，队列式定审' },
          { key: 'scripts', icon: PenLine, label: '待定稿脚本', count: s.pendingScripts, hint: '三版对比，选一版定稿' },
          { key: 'scripts', icon: PenLine, label: '待写脚本', count: s.unwrittenTopics, hint: '已定审选题可生成三版口播稿' },
          { key: 'articles', icon: FileText, label: '待写长文', count: s.pendingArticles, hint: '定稿脚本一键生成公众号深度文' },
        ])
      })
      .catch(() => setTodos([]))
  }, [])

  useEffect(() => {
    fetchMenuStats()
      .then((s) => setExtra({ watch: s.watch, style: s.style, benchmarksPending: s.benchmarksPending, benchmarksTodo: s.benchmarksTodo, publishing: s.publishing ?? 0, tasks: s.tasks }))
      .catch(() => {})
  }, [todos])

  const saveQuick = () =>
    (async () => {
      setError('')
      setQuickMsg('')
      try {
        const r = await api<{ id: number }>('/topics/quick', {
          method: 'POST',
          body: JSON.stringify({ title: quickTitle.trim(), audience: quickAudience }),
        })
        setQuickTitle('')
        setQuickMsg(`已记入选题库 #${r.id}，可去选题库深研`)
        const s = await fetchMenuStats()
        setExtra({ watch: s.watch, style: s.style, benchmarksPending: s.benchmarksPending, benchmarksTodo: s.benchmarksTodo, publishing: s.publishing ?? 0, tasks: s.tasks })
        setTodos((old) => old && old.map((t) => t.label === '待审选题' ? { ...t, count: s.draftTopics } : t))
      } catch (e) {
        setError(String(e))
      }
    })()

  const todoTotal = (todos ?? []).reduce((n, x) => n + x.count, 0)

  return (
    <div>
      <PageHeader icon={Lightbulb} title="工作台" desc="全流水线待办总览，点卡片直达对应队列" />

      {/* 采集链路状态横幅：仅异常时出现（探针每 15 分钟自动探测一次） */}
      {crawl && crawl.status !== 'ok' && crawl.status !== 'never' && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          <span className="font-medium">⚠ 抖音采集异常：{crawl.label}</span>
          <span className="text-amber-700/70">探测于 {crawl.at}{crawl.recent_failures > 0 && ` · 近 1 小时爬取失败 ${crawl.recent_failures} 次`}</span>
          <span className="text-amber-700/70">——新拆解可能失败，可走「本地视频拆解」上传，或等 IP 冷却后自动重试</span>
          <button onClick={() => onNavigate('settings')} className="ml-auto text-sky-600 hover:underline">去设置页看详情/立即检测</button>
        </div>
      )}

      {/* Hero */}
      <div className="relative overflow-hidden rounded-2xl border border-[#e4e4e7] bg-linear-to-br from-white to-[#f4f5fa] p-7 shadow-sm">
        <div className="absolute -top-20 -right-16 size-72 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="relative">
          <div className="mt-2 max-w-lg text-xl leading-snug font-semibold tracking-tight text-[#16161b]">
            {todos === null
              ? '从一句话 idea，到口播脚本、成片与公众号长文。'
              : todoTotal === 0
                ? '全链路没有欠账 🎉 去选题库深研一个新 idea 吧。'
                : `今天有 ${todoTotal} 件待办，按队列逐件清。`}
          </div>
          <div className="mt-3 max-w-xl text-[13px] leading-loose text-slate-500">
            选题（idea 深研 / 同行监控）→ 脚本工场 → 口播数字人 →
            公众号 → 发布物料包（发布永远人工）。
          </div>
          <div className="mt-5 flex flex-wrap gap-2.5">
            <button
              onClick={() => onNavigate('topics')}
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-sky-700"
            >
              <Lightbulb size={15} /> 深研一个 idea
            </button>
          </div>
        </div>
      </div>

      {/* 同行监测周报（近 7 天）：有产出才显示 */}
      {digest && (digest.new_videos > 0 || digest.top_topics.length > 0) && (
        <div className="card card-hover mt-4 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="badge bg-emerald-100 text-emerald-700">同行监测周报 · 近 7 天</span>
            <span className="text-xs text-slate-500">
              新视频 <b className="tabular-nums">{digest.new_videos}</b> 条 · 待定夺{' '}
              <b className="tabular-nums">{digest.pending}</b> 条 · 8 分以上选题 {digest.topics_7d} 个
            </span>
            <button onClick={() => onNavigate('benchmarks')} className="btn-ghost btn-xs ml-auto">去定夺</button>
            <button onClick={() => onNavigate('topics')} className="btn-ghost btn-xs">看选题库</button>
          </div>
          {digest.pending_authors.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-slate-500">
              {digest.pending_authors.map(([a, n]) => (
                <span key={a} className="rounded-full border border-slate-200 px-2 py-0.5">
                  {a} · {n} 条待定夺
                </span>
              ))}
            </div>
          )}
          {digest.discovered && digest.discovered.count > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
              <span className="font-medium text-emerald-700">清单外高赞同行 {digest.discovered.count} 个：</span>
              {digest.discovered.top.map((c) => (
                <span key={c.name} className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5">
                  {c.name} · {_fmtW(c.follower_count)}粉
                </span>
              ))}
              <button onClick={() => onNavigate('watch')} className="text-emerald-700 hover:underline">去定夺 →</button>
            </div>
          )}
          {digest.top_topics.length > 0 && (
            <div className="mt-3 space-y-1">
              {digest.top_topics.map((t) => (
                <button key={t.topic_id} onClick={() => onNavigate('topics')}
                  className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition hover:bg-slate-50">
                  <span className="badge shrink-0 bg-emerald-100 text-emerald-700 tabular-nums">{t.score.toFixed(1)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium text-zinc-900">{t.title}</span>
                    {t.angle && <span className="block truncate text-[11px] text-slate-400">切角：{t.angle}</span>}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          无法连接后端：{error}
        </div>
      )}

      {/* 待办卡片：直达各页队列 */}
      {todos !== null && (
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
          {todos.map((t) => {
            const Icon = t.icon
            const idle = t.count === 0
            return (
              <button
                key={t.label}
                onClick={() => onNavigate(t.key)}
                className={`card card-hover p-3.5 text-left ${idle ? 'opacity-60' : 'border-sky-200'}`}
              >
                <div className="flex items-center gap-2">
                  <Icon size={14} className={idle ? 'text-slate-400' : 'text-sky-600'} />
                  <span className="text-xs font-medium text-slate-500">{t.label}</span>
                  <span
                    className={`ml-auto text-xl font-bold tabular-nums ${
                      idle ? 'text-slate-300' : 'text-sky-600'
                    }`}
                  >
                    {t.count}
                  </span>
                </div>
                <div className="mt-2 line-clamp-2 text-[11px] leading-relaxed text-slate-400">{t.hint}</div>
              </button>
            )
          })}
        </div>
      )}

      {/* 快捷入口 */}
      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-zinc-900">快捷入口</span>
          {quickMsg && <span className="text-xs text-emerald-600">{quickMsg}</span>}
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <QuickTile icon={Lightbulb} label="快速记选题" hint="一句话记下，不深研"
                     active={showQuick} onClick={() => setShowQuick((v) => !v)} />
          <QuickTile icon={Radar} label="同行监测" hint={`${extra.watch} 个账号监控中`}
                     onClick={() => onNavigate('watch')} />
          <QuickTile icon={Fingerprint} label="我的风格" hint={extra.style ? `${extra.style} 条待拆解` : '持续研究我的账号'}
                     onClick={() => onNavigate('style')} />
          <QuickTile icon={Scissors} label="拆解库" hint={extra.benchmarksPending ? `${extra.benchmarksPending} 条待定夺` : `${extra.benchmarksTodo} 条待拆解`}
                     onClick={() => onNavigate('benchmarks')} />
          <QuickTile icon={Send} label="发布台" hint={extra.publishing ? `${extra.publishing} 个可打包` : '物料包（发布人工）'}
                     onClick={() => onNavigate('publishing')} />
          <QuickTile icon={Activity} label="任务监控" hint={extra.tasks ? `${extra.tasks} 个在管道` : '管道空闲'}
                     onClick={() => onNavigate('tasks')} />
        </div>
        {showQuick && (
          <div className="card mt-2 border-sky-200 bg-sky-50/40 p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={quickTitle}
                onChange={(e) => setQuickTitle(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && quickTitle.trim() && saveQuick()}
                placeholder="一句话记下选题，如：中小厂上 AI 先从质检切入"
                className="input min-w-64 flex-1"
              />
              <select
                value={quickAudience}
                onChange={(e) => setQuickAudience(e.target.value)}
                className="input max-w-36"
              >
                <option value="both">老板+FDE</option>
                <option value="boss">面向老板</option>
                <option value="fde">面向 FDE</option>
              </select>
              <button onClick={saveQuick} disabled={!quickTitle.trim()} className="btn-accent btn-xs disabled:opacity-40">
                记下来
              </button>
            </div>
            <div className="mt-1.5 text-[11px] text-slate-400">只入库存档，不触发 LLM 深研；进选题库后可再定审/深研。</div>
          </div>
        )}
      </div>
    </div>
  )
}
