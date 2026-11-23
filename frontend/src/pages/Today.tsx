import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Spark } from '../components'
import { fetchCardStats } from '../counts'
import HotComments from './HotComments'
import KbTab from './KbTab'
import PipelineTab from './PipelineTab'

/**
 * 今天（2026-11-23 驾驶舱定版）：四个 tab——
 * 驾驶舱=运营主轴（六列流程看板为主体 + 决策队列/行动清单/发布节奏，默认首 tab）；
 * 晨报=纯报表（指标/每日一篇/情报速览/昨夜管线）；热门评论=监控作者高赞评论→口播；知识库=未产出文档→长文图文。
 * 两条产线各占一 tab（源不同、产物不同），素材池归素材库。
 */

interface BriefItem { id: number; title?: string; kind?: string; line?: string }
interface CadenceRow { target: number; actual: number; gap: number }
interface Briefing {
  cadence: Record<string, CadenceRow>
  review: Record<string, { n: number; items: BriefItem[] }>
  action: {
    to_publish: { n: number; items: BriefItem[] }
    to_backfill: { content_id: number; title?: string; platform?: string }[]
    recent_results: { title?: string; platform?: string; verdict: string }[]
  }
  failed: number
}

interface MetricsResp {
  items: { date: string; fans?: number; play?: number; profile_views?: number }[]
}
interface SchedRow { key: string; label: string; last_status: string; last_run_at: string }
interface ArtRow { id: number; title: string; status: string }

const PIPELINES = ['watch_scan', 'card_line_daily', 'article_hotspots_daily', 'hl_search_daily']
const PIPE_LABEL: Record<string, string> = {
  watch_scan: '同行扫描',
  card_line_daily: '选题工场',
  article_hotspots_daily: '公众号直出',
  hl_search_daily: '组局挖掘（获客）',
}

type TodayTab = 'cockpit' | 'today' | 'comments' | 'kb'

const TABS: { id: TodayTab; label: string }[] = [
  { id: 'cockpit', label: '驾驶舱' },
  { id: 'today', label: '晨报' },
  { id: 'comments', label: '热门评论' },
  { id: 'kb', label: '知识库' },
]

export default function Today({ onNavigate, tab = 'cockpit', onTab }: {
  onNavigate: (k: string) => void
  tab?: string
  onTab?: (t: string) => void
}) {
  const active: TodayTab = (TABS.some((t) => t.id === tab) ? tab : 'cockpit') as TodayTab
  const [kpis, setKpis] = useState({ fans: 0, fansDelta: 0, play: 0, profileViews: 0 })
  const [fansHist, setFansHist] = useState<number[]>([])
  const [playHist, setPlayHist] = useState<number[]>([])
  const [todos, setTodos] = useState({ failed: 0, active: 0 })
  const [cands, setCands] = useState({ persona: 0, leadgen: 0 })
  const [error, setError] = useState('')
  const [pipes, setPipes] = useState<SchedRow[]>([])
  const [article, setArticle] = useState<ArtRow | null>(null)
  const [brief, setBrief] = useState<Briefing | null>(null)

  const reload = useCallback(async () => {
    setError('')
    try {
      try {
        const b = await api<Briefing>('/stats/briefing')
        setBrief(b)
      } catch { /* 晨报块留空 */ }
      const s = await fetchCardStats()
      setTodos({ failed: s.failed, active: s.tasks }) // tasks=在途、failed=终态（2026-11-11 口径分离，不再相减）
      // 候选待定夺两线真实数字：p_cand/l_cand 即 TodoStats.openCandidatesPersona/Leadgen 的透传
      setCands({ persona: s.p_cand, leadgen: s.l_cand })
      try {
        const sch = await api<{ items?: SchedRow[] } | SchedRow[]>('/schedules')
        const list = Array.isArray(sch) ? sch : (sch.items || [])
        setPipes(list.filter((x) => PIPELINES.includes(x.key)))
      } catch { /* 管线卡留空 */ }
      try {
        const arts = await api<{ items: ArtRow[] }>('/articles?limit=5')
        const gen = (arts.items || []).find((x) => x.status === 'generating')
        const ready = (arts.items || []).find((x) => ['draft', 'edited', 'rendered'].includes(x.status))
        setArticle(gen ?? ready ?? null)
      } catch { /* 长文卡留空 */ }
      try {
        const m = await api<MetricsResp>('/metrics/account')
        const items = (m.items || []).slice()
        const last = items[items.length - 1]
        const prev = items[items.length - 2]
        setKpis({
          fans: last?.fans ?? 0,
          fansDelta: last && prev ? (last.fans ?? 0) - (prev.fans ?? 0) : 0,
          play: last?.play ?? 0,
          profileViews: last?.profile_views ?? 0,
        })
        setFansHist(items.map((x) => x.fans ?? 0).slice(-30))
        setPlayHist(items.map((x) => x.play ?? 0).slice(-14))
      } catch { /* 指标卡保持 0 */ }
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">今天</h1>
      </div>

      <div className="mb-5 flex items-center gap-1.5 border-b border-[#E8ECF0] pb-2.5">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => onTab?.(t.id)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${active === t.id ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {active === 'comments' ? <HotComments />
        : active === 'kb' ? <KbTab mode="today" onNavigate={onNavigate} />
          : active === 'today' ? (
        <>
          {error && <div className="mb-3 text-sm text-red-600">{error}</div>}

          {/* ── 指标区：大数字 + 迷你趋势，无框，靠留白分隔 ── */}
          <div className="grid grid-cols-2 gap-x-8 gap-y-6 lg:grid-cols-3">
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">粉丝 · 施可号</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="num-serif text-[30px] font-semibold leading-none tracking-tight text-slate-900">{kpis.fans.toLocaleString()}</span>
                {kpis.fansDelta > 0 && <span className="text-[12px] font-medium text-emerald-600">+{kpis.fansDelta}</span>}
              </div>
              <div className="mt-2 text-slate-300"><Spark data={fansHist} /></div>
            </div>
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">昨日播放</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="num-serif text-[30px] font-semibold leading-none tracking-tight text-slate-900">{kpis.play.toLocaleString()}</span>
              </div>
              <div className="mt-2 text-slate-300"><Spark data={playHist} /></div>
            </div>
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">主页访问</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="num-serif text-[30px] font-semibold leading-none tracking-tight text-slate-900">{kpis.profileViews.toLocaleString()}</span>
              </div>
              <div className="mt-2 text-[11px] text-slate-300">平台 T-1 数据</div>
            </div>
          </div>

          <div className="my-6 border-t border-slate-100" />

          {/* ── 三块晨报：每日一篇 / 情报速览 / 昨夜管线 ── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <section className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
              <h3 className="text-[11px] font-medium uppercase tracking-wider text-slate-400">每日一篇 · 公众号</h3>
              {article
                ? <div className="mt-2">
                  <div className="text-[13.5px] font-medium leading-snug text-slate-800">{article.title}</div>
                  <div className={`mt-1 text-xs ${article.status === 'generating' ? 'text-sky-600' : 'text-emerald-600'}`}>
                    {article.status === 'generating' ? '生成中' : '已成文'}
                  </div>
                  <button onClick={() => onNavigate('article')} className="mt-2 text-[12px] text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-900">去改稿 →</button>
                </div>
                : <div className="mt-2 text-[13px] text-slate-300">今天没有直出（无相关热点空过）</div>}
            </section>

            <section className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
              <h3 className="text-[11px] font-medium uppercase tracking-wider text-slate-400">情报速览</h3>
              <div className="mt-2 space-y-1.5 text-[13px]">
                <div className="flex justify-between"><span className="text-slate-500">候选待定夺</span><span className="font-medium tabular-nums text-slate-900">{cands.persona}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">组局号名单</span><span className="font-medium tabular-nums text-slate-900">{cands.leadgen}</span></div>
              </div>
              <button onClick={() => onNavigate('l_cand')} className="mt-2 text-[12px] text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-900">去定夺 →</button>
            </section>

            <section className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
              <h3 className="text-[11px] font-medium uppercase tracking-wider text-slate-400">昨夜管线</h3>
              <div className="mt-2 space-y-1.5">
                {pipes.map((p) => {
                  const ok = (p.last_status || '').startsWith('ok')
                  return (
                    <div key={p.key} className="flex items-center gap-2 text-[13px]">
                      <span className={`size-1.5 shrink-0 rounded-full ${ok ? 'bg-emerald-500' : 'bg-red-400'}`} />
                      <span className="text-slate-600">{PIPE_LABEL[p.key] || p.label}</span>
                      <span className="ml-auto text-[11px] text-slate-300">{(p.last_run_at || '').slice(11, 16)}</span>
                    </div>
                  )
                })}
                {pipes.length === 0 && <div className="text-[13px] text-slate-300">—</div>}
              </div>
              <div className="mt-2 flex items-center gap-3 text-[12px]">
                <span className="text-slate-400">进行中 {todos.active}</span>
                {todos.failed > 0 && <span className="text-red-500">失败 {todos.failed}</span>}
                <button onClick={() => onNavigate('tasks')} className="ml-auto text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-900">任务页 →</button>
              </div>
            </section>
          </div>

        </>
      ) : (
        <>
          {/* ── 驾驶舱（2026-11-23 定版）：六列流程看板为主体 + 决策三块 ── */}
          <PipelineTab onNavigate={onNavigate} />
          {/* ── 决策队列 / 行动清单 / 发布节奏（R1/R3 迁入）：要我审的 + 要我做的 ── */}
          {brief && (
            <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
              <section className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                <h3 className="text-[11px] font-medium uppercase tracking-wider text-slate-400">决策队列 · 要我审</h3>
                <div className="mt-2 space-y-1.5 text-[13px]">
                  {([
                    ['scripts', '口播稿待终审', 'persona'],
                    ['notes', '笔记稿待终审', 'xhs'],
                    ['cards', '选题卡待备稿', 'persona'],
                    ['inbox', '素材收件箱', 'materials'],
                  ] as const).map(([k, label, nav]) => {
                    const b = brief.review[k]
                    return (
                      <button key={k} onClick={() => onNavigate(nav)}
                        className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left transition hover:bg-slate-50">
                        <span className="text-slate-500">{label}</span>
                        <span className={`num-serif tabular-nums ${b.n > 0 ? 'font-medium text-slate-900' : 'text-slate-300'}`}>{b.n}</span>
                        {b.items[0]?.title && <span className="min-w-0 flex-1 truncate text-[12px] text-slate-400">{b.items[0].title}</span>}
                      </button>
                    )
                  })}
                </div>
              </section>

              <section className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                <h3 className="text-[11px] font-medium uppercase tracking-wider text-slate-400">行动清单 · 要我做</h3>
                <div className="mt-2 space-y-1.5 text-[13px]">
                  <button onClick={() => onNavigate('article')}
                    className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left transition hover:bg-slate-50">
                    <span className="text-slate-500">该发布（定稿未登记）</span>
                    <span className={`num-serif tabular-nums ${brief.action.to_publish.n > 0 ? 'font-medium text-slate-900' : 'text-slate-300'}`}>{brief.action.to_publish.n}</span>
                    {brief.action.to_publish.items[0]?.title && <span className="min-w-0 flex-1 truncate text-[12px] text-slate-400">{brief.action.to_publish.items[0].title}</span>}
                  </button>
                  <button onClick={() => onNavigate('analytics')}
                    className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left transition hover:bg-slate-50">
                    <span className="text-slate-500">该补录（发布超 48h 未回流）</span>
                    <span className={`num-serif tabular-nums ${brief.action.to_backfill.length > 0 ? 'font-medium text-amber-700' : 'text-slate-300'}`}>{brief.action.to_backfill.length}</span>
                    {brief.action.to_backfill[0]?.title && <span className="min-w-0 flex-1 truncate text-[12px] text-slate-400">{brief.action.to_backfill[0].title}</span>}
                  </button>
                  {brief.action.recent_results.length > 0 && (
                    <div className="pt-1">
                      <div className="text-[12px] text-slate-400">近 7 天判卷结果</div>
                      {brief.action.recent_results.slice(0, 3).map((r, i) => (
                        <div key={i} className="flex items-center gap-2">
                          <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10.5px] ${r.verdict === 'viral' ? 'bg-violet-100 text-violet-700' : 'bg-emerald-100 text-emerald-700'}`}>
                            {r.verdict === 'viral' ? '爆款' : '过线'}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-600">{r.title}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </section>

              <section className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                <h3 className="text-[11px] font-medium uppercase tracking-wider text-slate-400">发布节奏 · 本周实发 vs 目标</h3>
                <div className="mt-2 space-y-1.5 text-[13px]">
                  {([
                    ['douyin', '抖音'],
                    ['wechat', '公众号'],
                    ['xhs', '小红书'],
                  ] as const).map(([k, label]) => {
                    const c = brief.cadence?.[k]
                    if (!c) return null
                    const behind = c.target > 0 && c.actual < c.target
                    return (
                      <div key={k} className="flex items-center gap-2">
                        <span className="text-slate-500">{label}</span>
                        <span className="num-serif tabular-nums font-medium text-slate-900">{c.actual}</span>
                        <span className="text-[11px] text-slate-300">/ 目标 {c.target > 0 ? c.target : '不限'}</span>
                        {behind && <span className="text-[11px] font-medium text-amber-600">还差 {c.target - c.actual}</span>}
                        {c.target > 0 && c.actual >= c.target && <span className="text-[11px] text-emerald-600">达标</span>}
                        <button onClick={() => onNavigate('schedule')} className="ml-auto text-[11px] text-slate-400 underline decoration-slate-200 underline-offset-2 hover:text-slate-700">排期 →</button>
                      </div>
                    )
                  })}
                  {(!brief.cadence?.douyin?.target && !brief.cadence?.wechat?.target && !brief.cadence?.xhs?.target) && (
                    <div className="text-[12px] text-slate-300">没设节奏目标（设置 · 发布节奏里定，晨报才开始报偏差）</div>
                  )}
                </div>
              </section>
            </div>
          )}
        </>
      )}
    </div>
  )
}
