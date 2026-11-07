import { useCallback, useEffect, useState } from 'react'
import { api, Topic } from '../api'
import { fetchMenuStats } from '../counts'

interface MetricsResp {
  items: { date: string; fans?: number; play?: number; profile_views?: number }[]
}
interface SchedRow { key: string; label: string; last_status: string; last_run_at: string; enabled: boolean }
interface ArtRow { id: number; title: string; status: string }

type CandType = 'persona' | 'leadgen'

const PIPELINES = ['article_hotspots_daily', 'watch_discover_daily', 'hl_search_daily']
const PIPE_LABEL: Record<string, string> = {
  article_hotspots_daily: '公众号直出', watch_discover_daily: '获客挖掘',
  hl_search_daily: '组局号搜索',
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
  const [todos, setTodos] = useState({ draft: 0, failed: 0, active: 0 })
  const [drafts, setDrafts] = useState<Topic[]>([])
  const [filter, setFilter] = useState<'all' | CandType>('all')
  const [quick, setQuick] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [pipes, setPipes] = useState<SchedRow[]>([])
  const [article, setArticle] = useState<ArtRow | null>(null)

  const reload = useCallback(async () => {
    setError('')
    try {
      const [tp, tl] = await Promise.all([
        api<{ items: Topic[] }>('/topics?status=draft&limit=200&line=persona'),
        api<{ items: Topic[] }>('/topics?status=draft&limit=200&line=leadgen'),
      ])
      const t = { items: [...(tp.items || []), ...(tl.items || [])] } as { items: Topic[] }
      const s = await fetchMenuStats()
      setTodos({ draft: s.today, failed: s.failed, active: Math.max(0, s.tasks - s.failed) })
      const ranked = (t.items || []).slice().sort((a, b) => (b.id ?? 0) - (a.id ?? 0))
      setDrafts(ranked)
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

  const act = async (t: Topic, action: 'approve' | 'reject') => {
    setBusy('处理中…')
    try {
      await api(`/topics/${t.id}/${action}`, { method: 'POST' })
      setDrafts((l) => l.filter((x) => x.id !== t.id))
      setBusy('')
    } catch (e) {
      setError(String(e)); setBusy('')
    }
  }

  const approveAndGenerate = async (t: Topic) => {
    const type: CandType = (t.evidence?.account_type === 'leadgen' ? 'leadgen' : 'persona')
    setBusy('批准并排队生成…')
    try {
      await api(`/topics/${t.id}/approve`, { method: 'POST' })
      await api('/scripts/generate', {
        method: 'POST',
        body: JSON.stringify({ topic_id: t.id, length: 'short', account_type: type }),
      })
      setDrafts((l) => l.filter((x) => x.id !== t.id))
      onNavigate(type === 'leadgen' ? 'l_topics' : 'p_topics')
      setBusy('')
    } catch (e) {
      setError(String(e)); setBusy('')
    }
  }

  const quickAdd = async () => {
    if (!quick.trim()) return
    setBusy('入库中…')
    try {
      await api('/topics/quick', { method: 'POST', body: JSON.stringify({ title: quick.trim(), audience: 'both', line: 'persona' }) })
      setQuick('')
      setBusy('已入库（draft）')
      await reload()
      setTimeout(() => setBusy(''), 1500)
    } catch (e) {
      setError(String(e)); setBusy('')
    }
  }

  const trending = () => {
    setBusy('热点雷达运行中（话题飙升 + 站外热点，几分钟后自动入库）…')
    ;(async () => {
      try {
        await api('/topics/trending', { method: 'POST' })
        setBusy('热点提炼已排队，稍后刷新查看')
      } catch (e) {
        setError(String(e)); setBusy('')
      }
    })()
  }

  const shown = drafts.filter((t) => filter === 'all' || (t.evidence?.account_type ?? 'persona') === filter)
  const nPersona = drafts.filter((t) => (t.evidence?.account_type ?? 'persona') === 'persona').length
  const nLeadgen = drafts.length - nPersona

  return (
    <div>
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">今日工作台</h1>
          <p className="mt-1 text-[13px] text-slate-400">管线 02:00 自动运转 · 指标 04:00 回流 · 雷达 06:00 已跑</p>
        </div>
        <button onClick={trending} className="rounded-lg bg-slate-900 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-slate-700">热点雷达</button>
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
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">本周成片</div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="num-serif text-[30px] font-semibold leading-none tracking-tight text-slate-900">3</span>
          </div>
          <div className="mt-2 text-[11px] text-slate-300">人设 2 · 获客 1</div>
        </div>
      </div>

      <div className="my-6 border-t border-slate-100" />

      {/* ── 主区：左「今天要拍」+ 右侧栏 ── */}
      <div className="grid grid-cols-1 gap-x-10 gap-y-6 lg:grid-cols-12">
        <div className="lg:col-span-8">
          <div className="flex items-center justify-between">
            <h2 className="text-[15px] font-semibold text-slate-900">今天要拍</h2>
            <div className="flex items-center gap-1">
              {(['all', 'persona', 'leadgen'] as const).map((k) => (
                <button key={k} onClick={() => setFilter(k)}
                  className={`rounded-md px-2.5 py-1 text-[12px] ${filter === k ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
                  {k === 'all' ? `全部 ${drafts.length}` : k === 'persona' ? `人设 ${nPersona}` : `获客 ${nLeadgen}`}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-3 flex gap-2">
            <input
              value={quick}
              onChange={(e) => setQuick(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') quickAdd() }}
              placeholder="快速记选题，回车入库…"
              className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm outline-none placeholder:text-slate-300 focus:border-slate-400"
            />
            <button onClick={quickAdd} disabled={!quick.trim()} className="shrink-0 rounded-lg bg-slate-900 px-4 py-2 text-[13px] font-medium text-white disabled:opacity-40">入库</button>
          </div>

          <div className="mt-3">
            {shown.length === 0 ? (
              <div className="py-10 text-center text-sm text-slate-300">待拍清单已清零——明晨管线会带来新一批</div>
            ) : (
              <div className="divide-y divide-slate-100">
                {shown.map((t) => {
                  const type: CandType = (t.evidence?.account_type === 'leadgen' ? 'leadgen' : 'persona')
                                  return (
                    <div key={t.id} className="group flex items-center gap-3 py-2.5">
                      <span className={`w-8 shrink-0 text-[11px] ${type === 'leadgen' ? 'text-amber-600' : 'text-slate-400'}`}>{LINE_TAG_ZH[type]}</span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13.5px] font-medium text-slate-800">{t.title}</div>
                      </div>
                      <span className="shrink-0 text-[11px] text-slate-300">{(t.created_at || '').slice(5, 10)}</span>
                      <button onClick={() => approveAndGenerate(t)}
                        className="shrink-0 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] text-slate-600 opacity-0 transition group-hover:opacity-100 hover:border-slate-400 hover:text-slate-900">
                        生成脚本
                      </button>
                      <button onClick={() => act(t, 'reject')}
                        className="shrink-0 rounded-md px-1.5 py-1 text-[12px] text-slate-300 opacity-0 transition group-hover:opacity-100 hover:text-slate-600">
                        ✕
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          <div className="mt-2 text-[11px] text-slate-300">行悬停出操作</div>
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
              <div className="flex justify-between"><span className="text-slate-500">候选待定夺</span><span className="font-medium tabular-nums text-slate-900">81</span></div>
              <div className="flex justify-between"><span className="text-slate-500">组局号名单</span><span className="font-medium tabular-nums text-slate-900">75</span></div>
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

const LINE_TAG_ZH: Record<CandType, string> = { persona: '人设', leadgen: '获客' }
