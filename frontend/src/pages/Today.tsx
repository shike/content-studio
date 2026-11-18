import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Spark } from '../components'
import { fetchCardStats } from '../counts'
import HotComments from './HotComments'
import KbTab from './KbTab'

/**
 * 今天（2026-11-18 用户定版）：三个平级 tab——
 * 晨报=指标/长文/管线（tab 名用户 2026-11-18 三次定稿：今日→数据→晨报）；热门评论=监控作者高赞评论→口播；知识库=未产出文档→长文图文。
 * 两条产线各占一 tab（源不同、产物不同），素材池归素材库。
 */

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

type TodayTab = 'today' | 'comments' | 'kb'

const TABS: { id: TodayTab; label: string }[] = [
  { id: 'today', label: '晨报' },
  { id: 'comments', label: '热门评论' },
  { id: 'kb', label: '知识库' },
]

export default function Today({ onNavigate, tab = 'today', onTab }: {
  onNavigate: (k: string) => void
  tab?: string
  onTab?: (t: string) => void
}) {
  const active: TodayTab = (TABS.some((t) => t.id === tab) ? tab : 'today') as TodayTab
  const [kpis, setKpis] = useState({ fans: 0, fansDelta: 0, play: 0, profileViews: 0 })
  const [fansHist, setFansHist] = useState<number[]>([])
  const [playHist, setPlayHist] = useState<number[]>([])
  const [todos, setTodos] = useState({ failed: 0, active: 0 })
  const [cands, setCands] = useState({ persona: 0, leadgen: 0 })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [pipes, setPipes] = useState<SchedRow[]>([])
  const [article, setArticle] = useState<ArtRow | null>(null)

  const reload = useCallback(async () => {
    setError('')
    try {
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

  const trending = () => {
    setBusy('选题工场运行中（五段流水线：信号采集→池子→主线→钩子→法官）…')
    ;(async () => {
      try {
        await api('/schedules/persona_idea_daily/run', { method: 'POST' })
        setBusy('工场已排队，靶子卡稍后进「人设号 · 选题」')
      } catch (e) {
        setError(String(e)); setBusy('')
      }
    })()
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">今天</h1>
        {active === 'today' && (
          <button onClick={trending} className="rounded-lg bg-slate-900 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-slate-700">选题工场</button>
        )}
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
          : (
        <>
          {error && <div className="mb-3 text-sm text-red-600">{error}</div>}
          {busy && <div className="mb-3 text-sm text-sky-700">{busy}</div>}

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
      )}
    </div>
  )
}
