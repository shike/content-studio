import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown, ChevronRight, Crosshair, Flame, RefreshCw, Sparkles, X,
} from 'lucide-react'
import { api, apiErrText } from '../api'
import { Busy, EmptyState, ErrorLine, PageHeader, relTime } from '../components'
import { usePageGuard } from '../hooks'

/** 今日靶子 = 选题工场靶子卡（PRD R1.4 三原则定版 2026-11-14）：
 *  每日 06:00 三车间流水线产卡（钩子/主线/流量三证/裁决四面），用户只做定夺与备稿。
 *  主线是写作纪律不出口；开场逐字用钩子。徽章口径 = draft 卡数（counts.ts targetsPersona）。 */

interface CardPool {
  domain_evidence?: string
  heat_evidence?: string
  heat_level?: string
  role?: string
  coverage?: string
  secondary?: boolean
  douyin_evidence?: { kind?: string; ref?: string; comments?: number; diggs?: number; spike_speed?: number; rank?: number } | null
  external_evidence?: { src?: string; title?: string } | null
  peer_covered?: boolean
}

interface CardJudge {
  hook_pull?: number
  spine_single?: number
  traffic_potential?: number
  verdict?: string
  reason?: string
}

interface CardGate { gate: string; pass: boolean; detail: string }

interface FalsifyItem {
  id: number
  claim: string
  target: string
  evidence_note: string
  result_note: string
  status: string // pending | testing | tested | scripted | dismissed
  auto_source: boolean
  script_id?: number | null
  created_at: string
}

const FSTATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: '待实测', cls: 'bg-slate-100 text-slate-600' },
  testing: { label: '实测中', cls: 'bg-amber-100 text-amber-800' },
  tested: { label: '已实测', cls: 'bg-emerald-100 text-emerald-700' },
  scripted: { label: '已成稿', cls: 'bg-sky-100 text-sky-700' },
}

interface TopicCard {
  id: number
  batch_date: string
  source: string
  pool: CardPool
  spine: string
  hook: string
  spice: string
  judge: CardJudge
  gates: CardGate[]
  status: string // draft | approved | discarded | jail
  script_id?: number | null
  superseded_by?: number | null
  created_at: string
}

const SOURCES: Record<string, { label: string; cls: string }> = {
  douyin_peer: { label: '监控号爆款', cls: 'bg-emerald-100 text-emerald-700' },
  radar: { label: '话题飙升', cls: 'bg-orange-100 text-orange-700' },
  douyin_hot: { label: '抖音热点榜', cls: 'bg-red-100 text-red-700' },
  external: { label: '外站热榜', cls: 'bg-slate-100 text-slate-500' },
  self_story: { label: '一线交付', cls: 'bg-indigo-100 text-indigo-700' },
  manual: { label: '手动投喂', cls: 'bg-violet-100 text-violet-700' },
}

const SPICE: Record<string, { label: string; cls: string }> = {
  low: { label: '辣度·低', cls: 'bg-slate-100 text-slate-500' },
  mid: { label: '辣度·中', cls: 'bg-amber-100 text-amber-800' },
  high: { label: '辣度·高', cls: 'bg-red-100 text-red-700' },
}

const COVERAGE: Record<string, string> = {
  boss: '老板盘', middle: '中层盘', job_wide: '岗位大盘', job_narrow: '窄圈',
}

const ANGLES = ['反常识判断', '身份定位喊话', '行动指令', '提问式', '对比打假', '数字落差']

function sourceTag(c: TopicCard) {
  const s = SOURCES[c.source] || { label: c.source, cls: 'bg-slate-100 text-slate-500' }
  const dy = (c.pool?.douyin_evidence || {}) as { comments?: number; diggs?: number; spike_speed?: number; rank?: number }
  let evidence = ''
  if (dy.comments != null) evidence = `评论 ${dy.comments}`
  else if (dy.spike_speed != null) evidence = `新增 ${dy.spike_speed} 条`
  else if (dy.rank != null) evidence = `榜 ${dy.rank}`
  return { ...s, evidence }
}

export default function HotTargets({ onNavigate }: { onNavigate: (k: string) => void }) {
  const [cards, setCards] = useState<TopicCard[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [fitems, setFitems] = useState<FalsifyItem[]>([])
  const [chip, setChip] = useState<'draft' | 'approved' | 'jail' | 'falsify'>('draft')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [fExpanded, setFExpanded] = useState<number | null>(null)
  const [fForm, setFForm] = useState({ evidence_note: '', result_note: '' })
  const [form, setForm] = useState({ hook: '', spine: '', angle: ANGLES[0] })
  const [seedOpen, setSeedOpen] = useState(false)
  const [seedText, setSeedText] = useState('')
  const [seedUrl, setSeedUrl] = useState('')
  const [fAddOpen, setFAddOpen] = useState(false)
  const [fNew, setFNew] = useState({ claim: '', target: '', evidence_note: '' })
  const [msg, setMsg] = useState('')
  const [booted, setBooted] = useState(false)
  const [factoryBusy, setFactoryBusy] = useState(false)
  const { guard, busy, error, setError } = usePageGuard()

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const load = useCallback(async () => {
    try {
      const [d, f] = await Promise.all([
        api<{ items: TopicCard[]; counts: Record<string, number> }>('/cards?limit=200'),
        api<{ items: FalsifyItem[] }>('/falsify?limit=200'),
      ])
      setCards(d.items || [])
      setCounts(d.counts || {})
      setFitems(f.items || [])
      setError('')
    } catch (e) {
      setError(apiErrText(e))
    } finally {
      setBooted(true)
    }
  }, [setError])

  useEffect(() => { load() }, [load])

  const pollJob = (jobId: number, type: string, done: (s: string, m: string) => void) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const d = await api<{ items: { id: number; status: string; progress: number; message: string; error: string | null }[] }>(
          `/jobs?limit=5&type=${type}`)
        const j = (d.items || []).find((x) => x.id === jobId)
        if (j && (j.status === 'running' || j.status === 'queued')) {
          setMsg(`${j.status === 'queued' ? '排队中' : `${j.progress || 0}%`} ${j.message || ''}`)
          return
        }
        if (pollRef.current) clearInterval(pollRef.current)
        pollRef.current = null
        done(j?.status || '?', (j?.error || j?.message || '').slice(0, 120))
        await load()
      } catch { /* 下轮再取 */ }
    }, 3000)
  }

  /** 手动投喂：一句话 → card_seed（锻主线→铸钩子→法官）→ 成 draft 卡或落 jail 带判词 */
  const feed = () =>
    guard(async () => {
      const d = await api<{ job_id: number }>('/cards/seed', {
        method: 'POST', body: JSON.stringify({ text: seedText.trim(), url: seedUrl.trim() }),
      })
      setSeedOpen(false)
      setSeedText('')
      setSeedUrl('')
      setMsg('投喂处理中（锻主线→铸钩子→法官，约 2~4 分钟）…')
      pollJob(d.job_id, 'card_seed', (st, err) => {
        setFactoryBusy(false)
        if (st === 'succeeded') setMsg('投喂成卡，已进待备稿 ✓')
        else setMsg(`投喂被拦：${err}（弃卡池可查）`)
      })
    })

  /** 立即开一轮工场（五段全链），不等 06:00 */
  const runFactory = () =>
    guard(async () => {
      setFactoryBusy(true)
      const d = await api<{ status: string; message: string }>('/schedules/card_line_daily/run', { method: 'POST' })
      setMsg(`工场已开跑（${d.message || ''}）…`)
      const jobId = Number((d.message || '').match(/#(\d+)/)?.[1] || 0)
      if (jobId) pollJob(jobId, 'card_daily', (st, err) => {
        setFactoryBusy(false)
        setMsg(st === 'succeeded' ? '工场跑完，靶子卡已更新 ✓' : `工场异常：${err}`)
      })
      else { setFactoryBusy(false); await load() }
    })

  const openCard = (c: TopicCard) => {
    const next = expanded === c.id ? null : c.id
    setExpanded(next)
    setForm({ hook: c.hook, spine: c.spine, angle: ANGLES[0] })
  }

  const compose = (c: TopicCard) =>
    guard(async () => {
      const d = await api<{ job_id: number; script_id: number }>(`/cards/${c.id}/compose`, {
        method: 'POST',
        body: JSON.stringify({ hook: form.hook.trim(), spine: form.spine.trim(), angle: form.angle }),
      })
      setExpanded(null)
      setMsg(`备稿已受理（脚本 #${d.script_id}，任务 #${d.job_id}）——稿子进内容看板待定稿`)
      await load()
    })

  const discard = (c: TopicCard) =>
    guard(async () => { await api(`/cards/${c.id}/discard`, { method: 'POST' }); await load() })

  const revive = (c: TopicCard) =>
    guard(async () => { await api(`/cards/${c.id}/revive`, { method: 'POST' }); await load() })

  /** 实测打假：添加 / 状态推进+结论回填 / 成稿 / 不做 */
  const addF = () =>
    guard(async () => {
      await api('/falsify', {
        method: 'POST',
        body: JSON.stringify({ claim: fNew.claim.trim(), target: fNew.target.trim(), evidence_note: fNew.evidence_note.trim() }),
      })
      setFAddOpen(false)
      setFNew({ claim: '', target: '', evidence_note: '' })
      setMsg('已进待实测清单 ✓')
      await load()
    })

  const updateF = (f: FalsifyItem, patch: { status?: string; result_note?: string; evidence_note?: string }) =>
    guard(async () => {
      await api(`/falsify/${f.id}`, { method: 'PUT', body: JSON.stringify(patch) })
      setMsg(patch.status === 'tested' ? '已实测 ✓ 可成稿' : '')
      await load()
    })

  const composeF = (f: FalsifyItem) =>
    guard(async () => {
      const d = await api<{ job_id: number; script_id: number }>(`/falsify/${f.id}/compose`, { method: 'POST' })
      setExpanded(null)
      setMsg(`实测稿已受理（脚本 #${d.script_id}，任务 #${d.job_id}）——进内容看板待定稿`)
      await load()
    })

  const dismissF = (f: FalsifyItem) =>
    guard(async () => { await api(`/falsify/${f.id}/dismiss`, { method: 'POST' }); await load() })

  const openF = (f: FalsifyItem) => {
    const next = fExpanded === f.id ? null : f.id
    setFExpanded(next)
    setFForm({ evidence_note: f.evidence_note || '', result_note: f.result_note || '' })
  }

  const activeF = fitems.filter((f) => f.status !== 'dismissed')
  const fList = activeF
  const cList = cards.filter((c) => c.status === chip)
  const draftN = counts.draft || cards.filter((c) => c.status === 'draft').length
  const chips: [typeof chip, string, number][] = [
    ['draft', '待备稿', counts.draft || 0],
    ['approved', '已备稿', counts.approved || 0],
    ['jail', `已拦·可捞`, counts.jail || 0],
    ['falsify', '实测打假', activeF.length],
  ]

  if (!booted) return <div className="text-sm text-slate-400">载入靶子卡…</div>

  return (
    <div>
      <PageHeader icon={Crosshair} title="今日靶子"
        desc="每天 06:00 选题工场产卡：钩子说给观众听（三秒说清+抓人），主线是写作纪律（全程论证、不出口），流量三证=域内×覆盖×热度。你只做两件事：定夺、备稿。" />
      {error && <div className="mt-4"><ErrorLine text={error} /></div>}

      {/* 回显带 */}
      <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[13px] text-slate-500">
        <span>待备稿 <span className="num-serif font-medium text-slate-700">{draftN}</span> 张 · 15 字三秒线 · 法官三维把门 · 弃卡 7 天可捞</span>
        {msg && <><span className="text-slate-300">·</span><span className="text-sky-600">{msg}</span></>}
        <div className="ml-auto flex items-center gap-2">
          <button className="btn-ghost btn-sm flex items-center gap-1" disabled={factoryBusy || !!busy}
            onClick={runFactory} title="立即开一轮三车间流水线（不等 06:00）">
            {factoryBusy ? <RefreshCw size={13} className="animate-spin" /> : <Flame size={13} />} 立即开工
          </button>
          <button className="btn-accent btn-sm flex items-center gap-1" disabled={!!busy || seedOpen}
            onClick={() => setSeedOpen(true)}>
            <Sparkles size={13} /> 手动投喂
          </button>
        </div>
      </div>

      {/* 手动投喂行内表单 */}
      {seedOpen && (
        <div className="mt-3 rounded-2xl border border-violet-200 bg-violet-50/50 p-4">
          <div className="text-[12px] text-violet-800">你看到想怼的事？一句话投进池子——系统锻主线、铸钩子、过法官，今天就能备稿。</div>
          <textarea className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 text-[13px] outline-none focus:border-violet-400"
            rows={2} maxLength={300} value={seedText}
            placeholder="一句话说清这事+你的判断（例：某公司发布 XX，砸的是谁的饭碗）"
            onChange={(e) => setSeedText(e.target.value)} />
          <div className="mt-2 flex items-center gap-2">
            <input className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-1.5 text-[12.5px] outline-none focus:border-violet-400"
              value={seedUrl} placeholder="相关链接（可选）"
              onChange={(e) => setSeedUrl(e.target.value)} />
            <button className="btn-accent btn-sm shrink-0" disabled={!seedText.trim() || !!busy} onClick={feed}>投进池子</button>
            <button className="btn-ghost btn-sm shrink-0" onClick={() => setSeedOpen(false)}>取消</button>
          </div>
        </div>
      )}

      {/* chips */}
      <div className="mt-4 flex flex-wrap items-center gap-2 text-[13px]">
        {chips.map(([k, label, n]) => (
          <button key={k}
            className={`rounded-full px-3 py-1 font-medium ${chip === k ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
            onClick={() => { setChip(k); setExpanded(null); setFExpanded(null) }}>
            {label} <span className="num-serif">{n}</span>
          </button>
        ))}
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}

      {chip === 'falsify' && (
        <div className="mt-3">
          <button className="btn-ghost btn-sm" disabled={!!busy} onClick={() => setFAddOpen(!fAddOpen)}>
            + 添加可疑主张
          </button>
          {fAddOpen && (
            <div className="mt-2 rounded-2xl border border-violet-200 bg-violet-50/50 p-4 space-y-2">
              <input className="w-full rounded-xl border border-slate-200 px-3 py-2 text-[13px] outline-none focus:border-violet-400"
                value={fNew.claim} maxLength={60}
                placeholder="要打的主张（例：某课号称 7 天学会接单）"
                onChange={(e) => setFNew((v) => ({ ...v, claim: e.target.value }))} />
              <div className="flex items-center gap-2">
                <input className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-1.5 text-[12.5px] outline-none focus:border-violet-400"
                  value={fNew.target} maxLength={40} placeholder="实测对象（例：某 AI 课）"
                  onChange={(e) => setFNew((v) => ({ ...v, target: e.target.value }))} />
                <button className="btn-accent btn-sm shrink-0" disabled={!fNew.claim.trim() || !!busy} onClick={addF}>进清单</button>
                <button className="btn-ghost btn-sm shrink-0" onClick={() => setFAddOpen(false)}>取消</button>
              </div>
              <input className="w-full rounded-xl border border-slate-200 px-3 py-1.5 text-[12.5px] outline-none focus:border-violet-400"
                value={fNew.evidence_note} placeholder="线索/链接（可选）"
                onChange={(e) => setFNew((v) => ({ ...v, evidence_note: e.target.value }))} />
            </div>
          )}
        </div>
      )}

      {chip === 'falsify' ? (
        fList.length === 0 ? (
          <div className="mt-8">
            <EmptyState icon={Crosshair} title="实测清单是空的"
              desc="把你想打假的对象一句话投进来；工场每天也会自动猎取候选（只猎一人可实测验证的：买得起、用得上、跑得完）。" />
          </div>
        ) : (
          <div className="mt-4 space-y-2">
            {fList.map((f) => {
              const st = FSTATUS[f.status] || { label: f.status, cls: 'bg-slate-100 text-slate-500' }
              const exp = fExpanded === f.id
              return (
                <div key={f.id} className={`rounded-2xl border bg-white transition ${exp ? 'border-emerald-300 shadow-sm' : 'border-[#E8ECF0]'}`}>
                  <div className="cursor-pointer px-4 py-3" onClick={() => openF(f)}>
                    <div className="flex items-center gap-2">
                      {exp ? <ChevronDown size={15} className="shrink-0 text-slate-400" />
                        : <ChevronRight size={15} className="shrink-0 text-slate-400" />}
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.label}</span>
                      {f.auto_source && (
                        <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] text-violet-700" title="工场自动猎取">工场猎取</span>
                      )}
                      <span className="hidden shrink-0 text-xs text-slate-400 sm:inline">{relTime(f.created_at || '')}</span>
                    </div>
                    <div className="mt-1 pl-6">
                      <div className="truncate text-[14.5px] font-bold leading-snug text-slate-900">{f.claim}</div>
                      {f.target && <div className="truncate text-[12.5px] text-slate-500">实测对象：{f.target}</div>}
                    </div>
                  </div>
                  {exp && (
                    <div className="border-t border-[#E8ECF0] px-4 py-4 space-y-3">
                      <div>
                        <div className="text-[11px] tracking-wide text-slate-400">线索/链接（可补记）</div>
                        <textarea className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-[12.5px] outline-none focus:border-emerald-400"
                          rows={2} value={fForm.evidence_note}
                          onChange={(e) => setFForm((v) => ({ ...v, evidence_note: e.target.value }))} />
                      </div>
                      <div>
                        <div className="text-[11px] tracking-wide text-slate-400">实测结论（过程+关键数字=成稿论据；没实测别填）</div>
                        <textarea className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-[12.5px] outline-none focus:border-emerald-400"
                          rows={3} value={fForm.result_note}
                          onChange={(e) => setFForm((v) => ({ ...v, result_note: e.target.value }))} />
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {f.status === 'pending' && (
                          <button className="btn-ghost btn-sm" disabled={!!busy}
                            onClick={(e) => { e.stopPropagation(); updateF(f, { status: 'testing' }) }}>开始实测</button>
                        )}
                        {(f.status === 'pending' || f.status === 'testing') && (
                          <button className="btn-accent btn-sm" disabled={!!busy || !fForm.result_note.trim()}
                            onClick={(e) => { e.stopPropagation(); updateF(f, { status: 'tested', result_note: fForm.result_note, evidence_note: fForm.evidence_note }) }}>
                            记完结论（已实测）
                          </button>
                        )}
                        {f.status === 'tested' && (
                          <>
                            <button className="btn-accent btn-sm" disabled={!!busy}
                              onClick={(e) => { e.stopPropagation(); composeF(f) }}>成稿（实测稿模板）</button>
                            <button className="btn-ghost btn-sm" disabled={!!busy}
                              onClick={(e) => { e.stopPropagation(); updateF(f, { result_note: fForm.result_note }) }}>保存结论</button>
                          </>
                        )}
                        {f.status === 'scripted' ? (
                          <button className="btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); onNavigate('p_content') }}>去内容看板 →</button>
                        ) : (
                          <button className="btn-ghost btn-sm flex items-center gap-1 text-slate-500" disabled={!!busy}
                            onClick={(e) => { e.stopPropagation(); dismissF(f) }}><X size={13} /> 不做</button>
                        )}
                        {f.status === 'tested' && !f.result_note.trim() && (
                          <span className="text-[11px] text-orange-600">结论为空不可成稿（没有实测事实=编造）</span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      ) : cList.length === 0 ? (
        <div className="mt-8">
          {chip === 'jail' ? (
            <EmptyState icon={Crosshair} title="弃卡池是空的" desc="法官判废的卡会落在这里 7 天——你可以翻案捞回，你的判断盖过法官。" />
          ) : chip === 'approved' ? (
            <EmptyState icon={Crosshair} title="还没有已备稿的卡" desc="在「待备稿」里展开一张卡，改钩子、选打法、出稿。" />
          ) : (
            <EmptyState icon={Crosshair} title="今天还没有靶子卡"
              desc="每天 06:00 工场自动产卡（宁缺毋滥，没得怼就是没得怼）；等不及点「立即开工」，或者手动投喂一条。" />
          )}
        </div>
      ) : (
        <div className="mt-4 space-y-2">
          {cList.map((c) => {
            const exp = expanded === c.id
            const src = sourceTag(c)
            const spice = SPICE[c.spice] || null
            const cov = COVERAGE[c.pool?.coverage || ''] || null
            const j = c.judge || {}
            return (
              <div key={c.id}
                className={`rounded-2xl border bg-white transition ${exp ? 'border-emerald-300 shadow-sm' : 'border-[#E8ECF0]'}`}>
                {/* 行：钩子第一行（你唯一听得到的件），主线第二行 */}
                <div className="cursor-pointer px-4 py-3" onClick={() => openCard(c)}>
                  <div className="flex items-center gap-2">
                    {exp ? <ChevronDown size={15} className="shrink-0 text-slate-400" />
                      : <ChevronRight size={15} className="shrink-0 text-slate-400" />}
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${src.cls}`}>{src.label}</span>
                    {src.evidence && <span className="shrink-0 text-[11px] text-slate-400">{src.evidence}</span>}
                    {c.pool?.peer_covered && (
                      <span className="shrink-0 rounded-full bg-sky-100 px-2 py-0.5 text-[11px] text-sky-700" title="同行已发过此题——选题已被市场验证">同行已发</span>
                    )}
                    <span className="hidden shrink-0 text-xs text-slate-400 sm:inline">{relTime(c.created_at || '')}</span>
                  </div>
                  <div className="mt-1 min-w-0 pl-6">
                    <div className="truncate text-[14.5px] font-bold leading-snug text-slate-900">
                      {chip === 'jail' ? c.hook || '（钩子未铸）' : c.hook}
                    </div>
                    <div className="truncate text-[12.5px] text-slate-500">主线：{c.spine}</div>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-6">
                    {cov && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">{cov}</span>}
                    {spice && <span className={`rounded-full px-2 py-0.5 text-[11px] ${spice.cls}`}>{spice.label}</span>}
                    {j.hook_pull != null && (
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${j.verdict === 'pass' ? 'bg-emerald-100 text-emerald-700' : 'bg-orange-100 text-orange-700'}`}
                        title={`法官三维：钩子吸引 ${j.hook_pull} / 主线单一 ${j.spine_single} / 流量潜力 ${j.traffic_potential}`}>
                        法官 {j.hook_pull}/{j.spine_single}/{j.traffic_potential}
                      </span>
                    )}
                  </div>
                </div>

                {exp && (
                  <div className="border-t border-[#E8ECF0] px-4 py-4">
                    <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
                      <div className="min-w-0 space-y-3">
                        {/* 左：备稿表单（钩子/主线可改写 + 打法六选一） */}
                        {c.status === 'draft' && (
                          <div className="space-y-2 rounded-xl border border-[#E8ECF0] bg-slate-50/60 p-3">
                            <div>
                              <div className="text-[11px] tracking-wide text-slate-400">开场钩子（说给观众听的第一句，可改）</div>
                              <input className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-[14px] font-semibold outline-none focus:border-emerald-400"
                                value={form.hook} maxLength={40}
                                onChange={(e) => setForm((f) => ({ ...f, hook: e.target.value }))} />
                            </div>
                            <div>
                              <div className="text-[11px] tracking-wide text-slate-400">主线半句话（写作纪律，不出口，可改）</div>
                              <input className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-[13px] outline-none focus:border-emerald-400"
                                value={form.spine} maxLength={40}
                                onChange={(e) => setForm((f) => ({ ...f, spine: e.target.value }))} />
                            </div>
                            <div>
                              <div className="text-[11px] tracking-wide text-slate-400">主打打法（选一个最贴的）</div>
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                {ANGLES.map((a) => (
                                  <button key={a}
                                    className={`rounded-full px-2.5 py-1 text-[12px] ${form.angle === a ? 'bg-slate-900 text-white' : 'bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100'}`}
                                    onClick={() => setForm((f) => ({ ...f, angle: a }))}>{a}</button>
                                ))}
                              </div>
                            </div>
                            <div className="flex items-center gap-2 pt-1">
                              <button className="btn-accent btn-sm" disabled={!!busy}
                                onClick={(e) => { e.stopPropagation(); compose(c) }}>
                                出稿（一版精品+自动打磨）
                              </button>
                              <button className="btn-ghost btn-sm flex items-center gap-1 text-slate-500"
                                disabled={!!busy} onClick={(e) => { e.stopPropagation(); discard(c) }}>
                                <X size={13} /> 不做
                              </button>
                              <span className="text-[11px] text-slate-400">稿子进内容看板待定稿，改完拍。</span>
                            </div>
                          </div>
                        )}
                        {c.status === 'approved' && (
                          <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-3 text-[13px] text-emerald-800">
                            已备稿（脚本 #{c.script_id}）。
                            <button className="btn-ghost btn-sm ml-2" onClick={(e) => { e.stopPropagation(); onNavigate('p_content') }}>
                              去内容看板 →
                            </button>
                          </div>
                        )}
                        {c.status === 'jail' && (
                          <div className="rounded-xl border border-orange-200 bg-orange-50/60 p-3">
                            <div className="text-[12.5px] text-orange-900">
                              <b>法官判词</b>：{j.reason || '—'}（三维 {j.hook_pull ?? '—'}/{j.spine_single ?? '—'}/{j.traffic_potential ?? '—'}）
                            </div>
                            <button className="btn-accent btn-sm mt-2" disabled={!!busy}
                              onClick={(e) => { e.stopPropagation(); revive(c) }}>
                              翻案捞回（你的判断盖过法官）
                            </button>
                          </div>
                        )}
                        {/* 裁决面：池子证据 + 闸记录 */}
                        <div className="space-y-1.5 text-[12.5px] text-slate-600">
                          <div><span className="text-[11px] text-slate-400">域内</span>：{c.pool?.domain_evidence || '—'}</div>
                          <div><span className="text-[11px] text-slate-400">热度</span>：{c.pool?.heat_evidence || '—'}</div>
                          {(c.gates || []).some((g) => !g.pass) && (
                            <div>
                              <span className="text-[11px] text-slate-400">闸记录</span>
                              <div className="mt-1 space-y-0.5">
                                {(c.gates || []).map((g, i) => (
                                  <div key={i} className={g.pass ? 'text-slate-400' : 'text-orange-600'}>
                                    {g.pass ? '✓' : '✗'} {g.gate}{g.detail ? `：${g.detail}` : ''}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                      {/* 右：四面速览 */}
                      <div className="space-y-2 text-[13px] text-slate-600">
                        <div className="rounded-xl border border-[#E8ECF0] p-3">
                          <div className="text-[11px] tracking-wide text-slate-400">流量三证</div>
                          <div className="mt-1 space-y-1 text-[12.5px]">
                            <div>域内：{c.pool?.domain_evidence ? '✓' : '—'}</div>
                            <div>覆盖：{cov || '—'}{c.pool?.role ? `（${c.pool.role}）` : ''}</div>
                            <div>热度：{c.pool?.heat_level || '—'}</div>
                          </div>
                          {j.reason && (
                            <div className="mt-2 border-t border-[#E8ECF0] pt-2 text-[12px] leading-relaxed">
                              <span className="text-[11px] text-slate-400">法官判词</span>
                              <div className="mt-0.5 text-slate-700">{j.reason}</div>
                            </div>
                          )}
                        </div>
                        <div className="text-[11px] leading-relaxed text-slate-400">
                          主线=整条视频的写作纪律，全程论证它但<b>不在视频里出口</b>；
                          钩子=前三秒说给观众的第一句，出稿时逐字使用（可在此改写）。
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
