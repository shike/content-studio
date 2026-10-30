import { useCallback, useEffect, useState } from 'react'
import { api, Topic } from '../api'
import { fetchMenuStats } from '../counts'

interface MetricsResp {
  items: { date: string; fans?: number; play?: number; profile_views?: number }[]
}

type CandType = 'persona' | 'leadgen'

const TYPE_TAG: Record<CandType, { label: string; cls: string }> = {
  persona: { label: '人设', cls: 'bg-sky-50 text-sky-700' },
  leadgen: { label: '获客打法', cls: 'bg-amber-100 text-amber-800' },
}

export default function Today({ onNavigate }: { onNavigate: (k: string) => void }) {
  const [kpis, setKpis] = useState({ fans: 0, fansDelta: 0, play: 0, profileViews: 0 })
  const [todos, setTodos] = useState({ draft: 0, failed: 0, active: 0 })
  const [drafts, setDrafts] = useState<Topic[]>([])
  const [filter, setFilter] = useState<'all' | CandType>('all')
  const [quick, setQuick] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [genProg, setGenProg] = useState<Record<number, { p: number; m: string }>>({})

  const reload = useCallback(async () => {
    setError('')
    try {
      const t = await api<{ items: Topic[] }>('/topics?status=draft&limit=200')
      const s = await fetchMenuStats()
      setTodos({ draft: s.draftTopics, failed: s.failed, active: Math.max(0, s.tasks - s.failed) })
      const ranked = (t.items || []).slice().sort((a, b) => {
        const qa = Number(a.evidence?.benchmark_quality_score ?? 0)
        const qb = Number(b.evidence?.benchmark_quality_score ?? 0)
        return qb - qa
      })
      setDrafts(ranked)
      try {  // 自有账号指标缺失（未配 self 账号）不影响候选定审
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
    const verb = action === 'approve' ? '批准' : '已标记不做'
    setBusy(`处理中…`)
    try {
      await api(`/topics/${t.id}/${action}`, { method: 'POST' })
      setDrafts((l) => l.filter((x) => x.id !== t.id))
      setBusy(`${verb}完成`)
      setTimeout(() => setBusy(''), 1500)
    } catch (e) {
      setError(String(e)); setBusy('')
    }
  }

  const approveAndGenerate = async (t: Topic) => {
    const type: CandType = (t.evidence?.account_type === 'leadgen' ? 'leadgen' : 'persona')
    setBusy('批准并排队生成…')
    try {
      await api(`/topics/${t.id}/approve`, { method: 'POST' })
      const r = await api<{ job_id: number }>('/scripts/generate', {
        method: 'POST',
        body: JSON.stringify({ topic_id: t.id, length: 'short', account_type: type }),
      })
      setDrafts((l) => l.filter((x) => x.id !== t.id))
      setGenProg((m) => ({ ...m, [t.id]: { p: 5, m: '脚本生成已排队' } }))
      void r
      onNavigate('persona')
      setBusy('')
    } catch (e) {
      setError(String(e)); setBusy('')
    }
  }

  const quickAdd = async () => {
    if (!quick.trim()) return
    setBusy('入库中…')
    try {
      await api('/topics/quick', { method: 'POST', body: JSON.stringify({ title: quick.trim(), audience: 'both' }) })
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
    guard(async () => {
      await api('/topics/trending', { method: 'POST' })
      setBusy('热点提炼已排队，稍后刷新查看')
    })
  }
  const guard = (fn: () => Promise<void>) => { fn().catch((e) => { setError(String(e)); setBusy('') }) }

  const shown = drafts.filter((t) => filter === 'all' || (t.evidence?.account_type ?? 'persona') === filter)
  const nPersona = drafts.filter((t) => (t.evidence?.account_type ?? 'persona') === 'persona').length
  const nLeadgen = drafts.length - nPersona

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">今日工作台</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400">指标 04:00 · 热点雷达 06:00 已跑</span>
          <button onClick={trending} className="btn-accent btn-sm">热点雷达</button>
        </div>
      </div>
      {error && <div className="mb-2 text-sm text-red-600">{error}</div>}
      {busy && <div className="mb-2 text-sm text-sky-700">{busy}</div>}

      <div className="mb-3 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        {[
          { l: '粉丝（施可号）', v: kpis.fans, d: kpis.fansDelta > 0 ? `+${kpis.fansDelta} 昨日` : '—' },
          { l: '昨日播放', v: kpis.play, d: '平台 T-1 数据', dim: true },
          { l: '主页访问', v: kpis.profileViews, d: '近 30 天序列', dim: true },
          { l: '本周成片', v: 3, d: '人设 2 · 获客 1', dim: true },
        ].map((x) => (
          <div key={x.l} className="rounded-2xl border border-[#e8ecf0] bg-white px-4 py-3">
            <div className="text-[11px] text-slate-400">{x.l}</div>
            <div className="num-serif mt-0.5 text-2xl font-semibold tabular-nums">{x.v}</div>
            <div className={`text-[11px] ${x.dim ? 'text-slate-400' : 'text-emerald-600'}`}>{x.d}</div>
          </div>
        ))}
      </div>

      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        <span className="rounded-full border border-[#e8ecf0] bg-white px-3 py-1.5">✏️ 选题待审 <b className="tabular-nums">{todos.draft}</b></span>
        <span className="rounded-full border border-[#e8ecf0] bg-white px-3 py-1.5">
          {todos.failed > 0 ? '⚠️' : '✅'} 失败任务 <b className="tabular-nums">{todos.failed}</b>
        </span>
        <span className="rounded-full border border-[#e8ecf0] bg-white px-3 py-1.5">🔄 进行中 <b className="tabular-nums">{todos.active}</b></span>
        <button onClick={() => onNavigate('intel')} className="rounded-full border border-[#e8ecf0] bg-white px-3 py-1.5 hover:bg-slate-50">
          📡 情报中心
        </button>
      </div>

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

      <div className="mb-2.5 flex items-center gap-2 text-xs text-slate-500">
        <b className="text-sm font-semibold text-zinc-900">今日候选</b>
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">人设 {nPersona}</span>
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">获客打法 {nLeadgen}</span>
        <span>按热度分排序 · 来源：同行自动拆解 + 热点雷达</span>
      </div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {[['all', '全部'], ['persona', '人设'], ['leadgen', '获客打法']].map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k as 'all' | CandType)}
            className={`rounded-full px-3 py-1 text-xs ${filter === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500'}`}>
            {label}
          </button>
        ))}
      </div>

      {shown.length === 0 ? (
        <div className="rounded-2xl border border-[#e8ecf0] bg-white p-8 text-center text-sm text-slate-400">
          今日候选已清零——批准的选题已进入对应产线；明晨 06:00 热点雷达会带来新一批。
        </div>
      ) : (
        shown.map((t) => {
          const type: CandType = (t.evidence?.account_type === 'leadgen' ? 'leadgen' : 'persona')
          const q = Number(t.evidence?.benchmark_quality_score ?? 0)
          const isNew = (t.created_at || '').slice(0, 10) === new Date().toISOString().slice(0, 10)
          const prog = genProg[t.id]
          return (
            <div key={t.id} className="mb-1.5 flex items-center gap-2.5 rounded-xl border border-[#e8ecf0] bg-white px-3.5 py-2.5">
              {isNew && <span className="shrink-0 rounded-full bg-emerald-500 px-1.5 py-px text-[10px] text-white">NEW</span>}
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${TYPE_TAG[type].cls}`}>{TYPE_TAG[type].label}</span>
              <div className="min-w-0 flex-1">
                <b className="block truncate text-[13px] font-medium">{t.title}</b>
                <span className="text-[11px] text-slate-400">
                  {(t.created_at || '').slice(5, 16).replace('T', ' ')}
                  {type === 'leadgen' && ' · 获客打法拆解'}
                  {type === 'persona' && t.evidence?.benchmark_quality_score != null && ' · 同行拆解沉淀'}
                </span>
                {prog && <div className="text-[11px] text-sky-700">{prog.m}</div>}
              </div>
              {t.evidence?.benchmark_quality_score != null && Number(t.evidence.benchmark_quality_score) > 0 && (
                <span className={`shrink-0 rounded-lg px-2 py-0.5 text-xs font-semibold tabular-nums ${Number(t.evidence.benchmark_quality_score) >= 6 ? 'bg-orange-100 text-orange-700' : 'bg-slate-100 text-slate-400'}`}>
                  热度 {Number(t.evidence.benchmark_quality_score).toFixed(1)}
                </span>
              )}
              <button onClick={() => approveAndGenerate(t)} className="mini-btn mini-yes">批准并生成</button>
              <button onClick={() => act(t, 'reject')} className="mini-btn">不做</button>
            </div>
          )
        })
      )}
      <div className="mu-note mt-2 text-[11px] text-slate-400">
        候选由同行扫描与今日热点自动拆解入库（热度分 = 赞粉比 × 赞数量）；批准后进入对应产线，「批准并生成」直接排队脚本。
      </div>
      <style>{`
        .mini-btn { border: 1px solid #e8ecf0; background: #fff; border-radius: 99px; padding: 4px 12px; font-size: 12px; font-family: inherit; color: #5b6472; flex-shrink: 0; cursor: pointer; }
        .mini-btn.mini-yes { background: #29c16a; color: #fff; border-color: #29c16a; }
      `}</style>
    </div>
  )
}
