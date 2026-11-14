import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { fetchMenuStats } from '../counts'

interface MetricsResp {
  items: { date: string; fans?: number; play?: number; profile_views?: number }[]
}
interface SchedRow { key: string; label: string; last_status: string; last_run_at: string; enabled: boolean }
interface ArtRow { id: number; title: string; status: string }

interface TargetCard { id: number; hook: string; spine: string; created_at: string }

const PIPELINES = ['watch_scan', 'card_line_daily', 'article_hotspots_daily', 'hl_search_daily']
const PIPE_LABEL: Record<string, string> = {
  watch_scan: '同行扫描',
  card_line_daily: '选题工场',
  article_hotspots_daily: '公众号直出',
  hl_search_daily: '组局挖掘（获客）',
}

/** 迷你趋势条：纯 SVG，无依赖，数据=近 N 天数值。 */
function Spark({ data, w = 96, h = 28 }: { data: number[]; w?: number; h?: number }) {
  if (!data.length) return null
  const max = Math.max(...data, 1)
  const min = Math.min(...data, 0)
  const span = max - min || 1
  const step = w / Math.max(data.length - 1, 1)
  const pts = data.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - ((v - min) / span) * (h - 4)).toFixed(1)}`)
  return (
    <svg width={w} height={h} className="overflow-visible">
      <polyline points={pts.join(' ')} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1].split(',')[0]} cy={pts[pts.length - 1].split(',')[1]} r="2" fill="currentColor" />
    </svg>
  )
}

export default function Today({ onNavigate }: { onNavigate: (k: string) => void }) {
  const [kpis, setKpis] = useState({ fans: 0, fansDelta: 0, play: 0, profileViews: 0 })
  const [fansHist, setFansHist] = useState<number[]>([])
  const [playHist, setPlayHist] = useState<number[]>([])
  const [todos, setTodos] = useState({ failed: 0, active: 0 })
  const [cands, setCands] = useState({ persona: 0, leadgen: 0 })
  const [targets, setTargets] = useState<TargetCard[]>([])
  const [quick, setQuick] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [pipes, setPipes] = useState<SchedRow[]>([])
  const [article, setArticle] = useState<ArtRow | null>(null)

  const reload = useCallback(async () => {
    setError('')
    try {
      const [cards, s] = await Promise.all([
        api<{ items: TargetCard[] }>('/cards?status=draft&limit=8'),
        fetchMenuStats(),
      ])
      setTodos({ failed: s.failed, active: s.tasks }) // tasks=在途、failed=终态（2026-11-11 口径分离，不再相减）
      // 候选待定夺两线真实数字：p_cand/l_cand 即 TodoStats.openCandidatesPersona/Leadgen 的透传
      setCands({ persona: s.p_cand, leadgen: s.l_cand })
      setTargets((cards.items || []).slice().sort((a, b) => (b.id ?? 0) - (a.id ?? 0)))
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

  const quickAdd = async () => {
    if (!quick.trim()) return
    setBusy('投喂已排队（锻主线→铸钩子→法官，几分钟）…')
    try {
      await api('/cards/seed', { method: 'POST', body: JSON.stringify({ text: quick.trim() }) })
      setQuick('')
      setBusy('投喂已排队，靶子卡稍后进「选题」页')
    } catch (e) {
      setError(String(e)); setBusy('')
    }
  }

  const trending = () => {
    setBusy('选题工场运行中（五段流水线：信号采集→池子→主线→钩子→法官）…')
    ;(async () => {
      try {
        await api('/schedules/card_line_daily/run', { method: 'POST' })
        setBusy('工场已排队，靶子卡稍后进「今日靶子」')
      } catch (e) {
        setError(String(e)); setBusy('')
      }
    })()
  }

  return (
    <div>
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">今日工作台</h1>
          <p className="mt-1 text-[13px] text-slate-400">管线 02:00 自动运转 · 指标 04:00 回流 · 雷达 06:00 已跑</p>
        </div>
        <button onClick={trending} className="rounded-lg bg-slate-900 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-slate-700">选题工场</button>
      </div>
      {error && <div className="mb-3 text-sm text-red-600">{error}</div>}
      {busy && <div className="mb-3 text-sm text-sky-700">{busy}</div>}

      {/* ── 指标区：大数字 + 迷你趋势，无框，靠留白分隔 ── */}
      <div className="grid grid-cols-2 gap-x-8 gap-y-6 lg:grid-cols-4">
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

      {/* ── 主区：左「今天要拍」+ 右侧栏 ── */}
      <div className="grid grid-cols-1 gap-x-10 gap-y-6 lg:grid-cols-12">
        <div className="lg:col-span-8">
          <div className="flex items-center justify-between">
            <h2 className="text-[15px] font-semibold text-slate-900">待备稿靶子卡</h2>
            <button onClick={() => onNavigate('p_topics')} className="text-[12px] text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-900">去选题 →</button>
          </div>

          <div className="mt-3 flex gap-2">
            <input
              value={quick}
              onChange={(e) => setQuick(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') quickAdd() }}
              placeholder="一句话投喂选题（锻完进「选题」页待备稿）…"
              className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm outline-none placeholder:text-slate-300 focus:border-slate-400"
            />
            <button onClick={quickAdd} disabled={!quick.trim()} className="shrink-0 rounded-lg bg-slate-900 px-4 py-2 text-[13px] font-medium text-white disabled:opacity-40">投喂</button>
          </div>

          <div className="mt-3">
            {targets.length === 0 ? (
              <div className="py-10 text-center text-sm text-slate-300">待备稿已清零——明晨管线会带来新一批靶子卡</div>
            ) : (
              <div className="divide-y divide-slate-100">
                {targets.map((c) => (
                  <div key={c.id} className="group flex cursor-pointer items-center gap-3 py-2.5"
                    onClick={() => onNavigate('p_topics')}>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13.5px] font-medium text-slate-800">{c.hook || '（钩子未铸）'}</div>
                      <div className="mt-0.5 truncate text-[12px] text-slate-400">主线：{c.spine}</div>
                    </div>
                    <span className="shrink-0 text-[11px] text-slate-300">{(c.created_at || '').slice(5, 10)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="mt-2 text-[11px] text-slate-300">备稿（出稿/不做）在「选题」页操作</div>
        </div>

        {/* 右侧栏 */}
        <div className="space-y-8 lg:col-span-4">
          <section>
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

          <section>
            <h3 className="text-[11px] font-medium uppercase tracking-wider text-slate-400">情报速览</h3>
            <div className="mt-2 space-y-1.5 text-[13px]">
              <div className="flex justify-between"><span className="text-slate-500">候选待定夺</span><span className="font-medium tabular-nums text-slate-900">{cands.persona}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">组局号名单</span><span className="font-medium tabular-nums text-slate-900">{cands.leadgen}</span></div>
            </div>
            <button onClick={() => onNavigate('l_cand')} className="mt-2 text-[12px] text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-900">去定夺 →</button>
          </section>

          <section>
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
      </div>
    </div>
  )
}

