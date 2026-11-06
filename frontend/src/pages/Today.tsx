import { useCallback, useEffect, useState } from 'react'
import { api, Topic } from '../api'
import { fetchMenuStats } from '../counts'

interface MetricsResp {
  items: { date: string; fans?: number; play?: number; profile_views?: number }[]
}
interface SchedRow { key: string; label: string; last_status: string; last_run_at: string; enabled: boolean }
interface ArtRow { id: number; title: string; status: string }

type CandType = 'persona' | 'leadgen'

const LINE_TAG: Record<CandType, { label: string; cls: string }> = {
  persona: { label: '人设', cls: 'bg-slate-100 text-slate-600' },
  leadgen: { label: '获客', cls: 'bg-[#fef3c7] text-amber-800' },
}

const PIPELINES = ['article_hotspots_daily', 'watch_discover_daily', 'hl_search_daily', 'hl_funnel_daily', 'topic_sharp_daily']
const PIPE_LABEL: Record<string, string> = {
  article_hotspots_daily: '公众号直出', watch_discover_daily: '获客挖掘',
  hl_search_daily: '组局号搜索', hl_funnel_daily: '漏斗切角', topic_sharp_daily: '锐度评分',
}

export default function Today({ onNavigate }: { onNavigate: (k: string) => void }) {
  const [kpis, setKpis] = useState({ fans: 0, fansDelta: 0, play: 0, profileViews: 0 })
  const [todos, setTodos] = useState({ draft: 0, failed: 0, active: 0 })
  const [sharp, setSharp] = useState({ persona: 0, leadgen: 0 })
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
      setTodos({ draft: s.draftTopics, failed: s.failed, active: Math.max(0, s.tasks - s.failed) })
      setSharp({ persona: s.sharpPersona ?? 0, leadgen: s.sharpLeadgen ?? 0 })
      const ranked = (t.items || []).slice().sort((a, b) => {
        const sa = a.sharpness ?? -1
        const sb = b.sharpness ?? -1
        if (sa !== sb) return sb - sa
        const qa = Number(a.evidence?.benchmark_quality_score ?? 0)
        const qb = Number(b.evidence?.benchmark_quality_score ?? 0)
        return qb - qa
      })
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
      onNavigate(type === 'leadgen' ? 'leadgen' : 'persona')
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
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">今日工作台</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400">指标 04:00 · 管线 02:00 · 雷达 06:00 已跑</span>
          <button onClick={trending} className="btn-accent btn-sm">热点雷达</button>
        </div>
      </div>
      {error && <div className="mb-2 text-sm text-red-600">{error}</div>}
      {busy && <div className="mb-2 text-sm text-sky-700">{busy}</div>}

      {/* ── 指标带：一张卡，竖线分隔六格 ── */}
      <div className="mb-4 grid grid-cols-2 divide-[#e8ecf0] rounded-2xl border border-[#e8ecf0] bg-white sm:grid-cols-3 sm:divide-x lg:grid-cols-6">
        {[
          { l: '粉丝（施可号）', v: kpis.fans, d: kpis.fansDelta > 0 ? `+${kpis.fansDelta} 昨日` : '—', good: kpis.fansDelta > 0 },
          { l: '昨日播放', v: kpis.play, d: '平台 T-1', dim: true },
          { l: '主页访问', v: kpis.profileViews, d: '近 30 天序列', dim: true },
          { l: '三锐池（待拍）', v: sharp.persona + sharp.leadgen, d: `人设 ${sharp.persona} · 获客 ${sharp.leadgen}`, dim: false },
          { l: '候选待定夺', v: todos.draft, d: '全部 draft 选题', dim: true },
          { l: '本周成片', v: 3, d: '人设 2 · 获客 1', dim: true },
        ].map((x, i) => (
          <div key={x.l} className={`px-4 py-3 ${i >= 2 ? 'hidden sm:block' : ''} ${i >= 4 ? 'hidden lg:block' : ''}`}>
            <div className="text-[11px] text-slate-400">{x.l}</div>
            <div className="num-serif mt-0.5 text-[22px] font-semibold tabular-nums text-slate-900">{x.v}</div>
            <div className={`text-[11px] ${x.good ? 'text-emerald-600' : 'text-slate-400'}`}>{x.d}</div>
          </div>
        ))}
      </div>

      {/* ── 主区：左「今天要拍」7 列 + 右「情报/系统」5 列 ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7">
          {/* 快速记题 */}
          <div className="mb-3 flex gap-2">
            <input
              value={quick}
              onChange={(e) => setQuick(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') quickAdd() }}
              placeholder="快速记选题：想到什么直接打一句话，回车入库…"
              className="w-full rounded-xl border border-[#e8ecf0] bg-white px-4 py-2 text-sm outline-none placeholder:text-slate-400 focus:border-sky-300"
            />
            <button onClick={quickAdd} disabled={!quick.trim()} className="btn-accent btn-sm disabled:opacity-40">入库</button>
          </div>

          <div className="mb-2 flex items-center gap-2">
            <b className="text-sm font-semibold text-zinc-900">今天要拍</b>
            <span className="rounded-full bg-[#0a2f24] px-2 py-0.5 text-[10px] font-medium text-[#e8c97f]">按锐度排序</span>
            <div className="ml-auto flex gap-1.5">
              {[['all', '全部'], ['persona', '人设'], ['leadgen', '获客']].map(([k, label]) => (
                <button key={k} onClick={() => setFilter(k as 'all' | CandType)}
                  className={`rounded-full px-3 py-1 text-xs ${filter === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500'}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>

          {shown.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-[#e3e8ed] bg-white p-8 text-center text-sm text-slate-400">
              待拍清单已清零——批准的选题已进入对应产线；明晨管线会带来新一批。
            </div>
          ) : (
            <div className="overflow-hidden rounded-2xl border border-[#e8ecf0] bg-white">
              {shown.map((t, idx) => {
                const type: CandType = (t.evidence?.account_type === 'leadgen' ? 'leadgen' : 'persona')
                const q = Number(t.evidence?.benchmark_quality_score ?? 0)
                const sharp = t.sharpness
                const isNew = (t.created_at || '').slice(0, 10) === new Date().toISOString().slice(0, 10)
                return (
                  <div key={t.id} className={`flex items-center gap-2.5 px-3.5 py-2.5 ${idx > 0 ? 'border-t border-[#f1f4f7]' : ''}`}>
                    {isNew && <span className="shrink-0 rounded-full bg-emerald-500 px-1.5 py-px text-[10px] text-white">NEW</span>}
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${LINE_TAG[type].cls}`}>{LINE_TAG[type].label}</span>
                    <div className="min-w-0 flex-1">
                      <b className="block truncate text-[13px] font-medium">{t.title}</b>
                      <span className="text-[11px] text-slate-400">
                        {(t.created_at || '').slice(5, 16).replace('T', ' ')}
                        {type === 'leadgen' && ' · 获客打法'}
                      </span>
                    </div>
                    {sharp != null ? (
                      <span className={`shrink-0 rounded-lg px-2 py-0.5 text-xs font-semibold tabular-nums ${sharp >= 8 ? 'bg-[#0a2f24] text-[#e8c97f]' : sharp >= 7 ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-400'}`}>
                        锐 {sharp}
                      </span>
                    ) : q > 0 ? (
                      <span className="shrink-0 rounded-lg bg-slate-100 px-2 py-0.5 text-xs tabular-nums text-slate-500">热度 {q.toFixed(1)}</span>
                    ) : null}
                    <button onClick={() => approveAndGenerate(t)} className="btn-accent btn-xs shrink-0">拍这条</button>
                    <button onClick={() => act(t, 'reject')} className="btn-ghost btn-xs shrink-0">不做</button>
                  </div>
                )
              })}
            </div>
          )}
          <div className="mt-2 px-1 text-[11px] text-slate-400">
            候选由同行扫描、热点雷达与漏斗引擎自动入库；「拍这条」= 批准并排队生成一版精品脚本。
          </div>
        </div>

        <div className="space-y-4 lg:col-span-5">
          {/* 公众号今日长文 */}
          <section className="rounded-2xl border border-[#e8ecf0] bg-white p-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="rounded bg-[#0a2f24] px-2 py-0.5 text-[10px] font-medium text-[#e8c97f]">每日一篇</span>
              <span className="text-[11px] text-slate-400">凌晨直出 · 改稿发布在公众号线</span>
            </div>
            {article
              ? <>
                <div className="text-[15px] font-semibold leading-snug text-slate-900">{article.title}</div>
                <div className={`mt-1.5 text-xs ${article.status === 'generating' ? 'text-sky-600' : 'text-emerald-600'}`}>
                  {article.status === 'generating' ? '生成中——明早来看成稿' : '已成文 · 去公众号线改稿渲染后发布'}
                </div>
                <button onClick={() => onNavigate('article')} className="btn-accent btn-xs mt-2.5">去改稿</button>
              </>
              : <div className="text-[13px] text-slate-400">今天还没有直出长文（没有值得写的热点会空过）</div>}
          </section>

          {/* 情报速览 */}
          <section className="rounded-2xl border border-[#e8ecf0] bg-white p-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">情报速览</span>
              <span className="text-[11px] text-slate-400">凌晨管线产出</span>
            </div>
            <div className="space-y-2 text-[13px]">
              <div className="flex items-center justify-between">
                <span className="text-slate-600">候选待定夺</span>
                <span className="tabular-nums font-medium text-slate-900">{todos.draft > 0 ? drafts.length : 0} 新 + 存量</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-600">名单号（组局同行）</span>
                <span className="text-slate-900">75</span>
              </div>
              <button onClick={() => onNavigate('leadgen')} className="btn-ghost btn-xs w-full">去挖号情报定夺</button>
            </div>
          </section>

          {/* 昨夜管线 */}
          <section className="rounded-2xl border border-[#e8ecf0] bg-white p-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">昨夜管线</span>
              <span className="ml-auto text-[11px] text-slate-400">
                {todos.failed > 0 ? <span className="text-red-500">{todos.failed} 失败</span> : <span className="text-emerald-600">全部正常</span>}
                {' · '}进行中 {todos.active}
              </span>
            </div>
            <div className="space-y-1.5">
              {pipes.map((p) => {
                const ok = (p.last_status || '').startsWith('ok')
                return (
                  <div key={p.key} className="flex items-center gap-2 text-[12.5px]">
                    <span className={`size-1.5 shrink-0 rounded-full ${ok ? 'bg-emerald-500' : 'bg-red-400'}`} />
                    <span className="text-slate-600">{PIPE_LABEL[p.key] || p.label}</span>
                    <span className="ml-auto text-[11px] text-slate-300">{(p.last_run_at || '').slice(5, 16).replace('T', ' ')}</span>
                  </div>
                )
              })}
              {pipes.length === 0 && <div className="text-xs text-slate-300">加载中…</div>}
            </div>
            <button onClick={() => onNavigate('tasks')} className="btn-ghost btn-xs mt-2.5 w-full">去任务页</button>
          </section>
        </div>
      </div>
    </div>
  )
}
