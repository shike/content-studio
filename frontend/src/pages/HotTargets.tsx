import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ChevronDown, ChevronRight, Clapperboard, Crosshair, Flame, RefreshCw, X,
} from 'lucide-react'
import { api, apiErrText, Topic } from '../api'
import { Busy, EmptyState, ErrorLine, PageHeader, relTime } from '../components'
import { usePageGuard } from '../hooks'

/** 今日靶子（2026-11-11 观点怼热点主轴）：每天 06:00 热点×FDE 关联评出的可怼靶子。
 *  选中切角 → 备三版口播稿（内容看板定稿）——人设号选题库的主形态。
 *  徽章口径 = 待备稿靶子数（counts.ts targetsPersona）。 */

type ChipKey = 'todo' | 'gen' | 'past' | 'no' | 'bench'

const SPICE: Record<string, { label: string; cls: string }> = {
  low: { label: '辣度·低', cls: 'bg-slate-100 text-slate-500' },
  mid: { label: '辣度·中', cls: 'bg-amber-100 text-amber-800' },
  high: { label: '辣度·高', cls: 'bg-red-100 text-red-700' },
}

function heatTag(hotspot: string): { label: string; cls: string } | null {
  if (/百度热搜/.test(hotspot)) return { label: '百度热搜', cls: 'bg-orange-100 text-orange-700' }
  if (/IT之家|爱范儿|科技媒体|36kr|36氪/.test(hotspot)) return { label: '科技媒体', cls: 'bg-sky-100 text-sky-700' }
  if (/检索/.test(hotspot)) return { label: '联网检索', cls: 'bg-indigo-100 text-indigo-700' }
  return null
}

/** 热度证据（三版定版）：target.heat 优先（如「百度热搜第23位」），无则回落来源解析 */
function heatEvidence(t: Topic): { text: string; onBoard: boolean } | null {
  const h = String(t.evidence?.target?.heat || '').trim()
  if (h) return { text: h, onBoard: /热搜第/.test(h) }
  const tag = heatTag(t.source_ref || '')
  if (!tag) return null
  return { text: `${tag.label}（未在榜·备胎）`, onBoard: tag.label === '百度热搜' }
}

interface ScriptRow { id: number; topic_id: number | null; status: string }

export default function HotTargets({ onNavigate }: { onNavigate: (k: string) => void }) {
  const [trending, setTrending] = useState<Topic[]>([])   // draft 靶子（全部日期）
  const [rejected, setRejected] = useState<Topic[]>([])   // trending 已不怼
  const [bench, setBench] = useState<Topic[]>([])         // 拆解产题等其他进料 draft
  const [scriptsMap, setScriptsMap] = useState<Record<number, string>>({}) // topic_id→脚本状态
  const [chip, setChip] = useState<ChipKey>('todo')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [detail, setDetail] = useState<Record<number, Topic>>({})
  const [booted, setBooted] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshMsg, setRefreshMsg] = useState('')
  const { guard, busy, error, setError } = usePageGuard()

  const today = new Date().toISOString().slice(0, 10)

  const load = useCallback(async () => {
    try {
      const [d, r, sc] = await Promise.all([
        api<{ items: Topic[] }>('/topics?status=draft&line=persona&limit=200&order=new'),
        api<{ items: Topic[] }>('/topics?status=rejected&line=persona&source=trending&limit=100'),
        api<{ items: ScriptRow[] }>('/scripts?account_type=persona&limit=300'),
      ])
      setTrending(d.items.filter((t) => t.source_type === 'trending'))
      setBench(d.items.filter((t) => t.source_type !== 'trending'))
      setRejected(r.items)
      const map: Record<number, string> = {}
      for (const s of sc.items || []) if (s.topic_id) map[s.topic_id] = s.status
      setScriptsMap(map)
      setError('')
    } catch (e) {
      setError(apiErrText(e))
    } finally {
      setBooted(true)
    }
  }, [setError])

  useEffect(() => { load() }, [load])

  const detailOf = useCallback((id: number): Topic | null =>
    detail[id] ?? trending.find((t) => t.id === id) ?? rejected.find((t) => t.id === id) ?? null,
    [detail, trending, rejected])

  const openRow = (t: Topic) => {
    const next = expanded === t.id ? null : t.id
    setExpanded(next)
    if (next != null && !detail[t.id]) {
      api<Topic>(`/topics/${t.id}`).then((full) => setDetail((c) => ({ ...c, [t.id]: full })))
        .catch((e) => setError(apiErrText(e)))
    }
  }

  /* 手动刷新：触发提炼任务 → 3s 轮询直到跑完 → 重载 */
  const refresh = async () => {
    setRefreshing(true)
    setRefreshMsg('已提交提炼任务…')
    try {
      await api('/topics/trending', { method: 'POST' })
      const poll = setInterval(async () => {
        try {
          const d = await api<{ items: { status: string; progress: number; message: string }[] }>(
            '/jobs?limit=3&type=trending_topics')
          const run = (d.items || []).find((j) => j.status === 'running' || j.status === 'queued')
          if (run) {
            setRefreshMsg(`提炼中 ${run.progress || 0}% ${run.message || ''}`)
            return
          }
          clearInterval(poll)
          setRefreshing(false)
          setRefreshMsg('')
          await load()
        } catch { /* 下轮再取 */ }
      }, 3000)
    } catch (e) {
      setRefreshing(false)
      setRefreshMsg('')
      setError(apiErrText(e))
    }
  }

  const dismiss = (t: Topic) =>
    guard(async () => {
      await api(`/topics/${t.id}/reject`, { method: 'POST' })
      await load()
    })

  const genScripts = (t: Topic) =>
    guard(async () => {
      if (t.title) await api(`/topics/${t.id}/angle`, { method: 'PUT', body: JSON.stringify({ angle: t.title }) })
      await api('/scripts/generate', {
        method: 'POST',
        body: JSON.stringify({
          topic_id: t.id, angles: ['反常识判断', '对比打假', '提问式'],
          account_type: 'persona', use_style: true,
        }),
      })
      await load()
    })

  /* 分组 */
  const gen = useMemo(() => trending.filter((t) => scriptsMap[t.id]), [trending, scriptsMap])
  const todo = useMemo(() => trending.filter(
    (t) => !scriptsMap[t.id] && (t.created_at || '').slice(0, 10) === today), [trending, scriptsMap, today])
  const past = useMemo(() => trending.filter(
    (t) => !scriptsMap[t.id] && (t.created_at || '').slice(0, 10) !== today), [trending, scriptsMap, today])

  const list: Topic[] = chip === 'todo' ? todo : chip === 'gen' ? gen
    : chip === 'past' ? past : chip === 'no' ? rejected : bench

  const chips: [ChipKey, string, number][] = [
    ['todo', '待备稿', todo.length], ['gen', '已备稿', gen.length],
    ['past', '往期', past.length], ['no', '不怼', rejected.length],
    ['bench', '拆解产题', bench.length],
  ]

  if (!booted) return <div className="text-sm text-slate-400">载入今日靶子…</div>

  return (
    <div>
      <PageHeader icon={Crosshair} title="今日靶子"
        desc="每天 06:00 抓热点×FDE 关联，评出值得怼的靶子——选中切角直接备三版口播稿，改完拍。观点怼热点是人设号唯一主轴。" />
      {error && <div className="mt-4"><ErrorLine text={error} /></div>}

      {/* 回显带 */}
      <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[13px] text-slate-500">
        <span>今日 <span className="num-serif font-medium text-slate-700">{todo.length}</span> 条待备稿 · 昨日靶子已按批次自动让位 · 14 天禁撞已校验</span>
        {refreshMsg && <><span className="text-slate-300">·</span><span className="text-sky-600">{refreshMsg}</span></>}
        <button className="btn-ghost btn-sm ml-auto flex items-center gap-1" disabled={refreshing || !!busy} onClick={refresh}>
          {refreshing ? <RefreshCw size={13} className="animate-spin" /> : <Flame size={13} />} 手动刷新靶子
        </button>
      </div>

      {/* chips */}
      <div className="mt-4 flex flex-wrap items-center gap-2 text-[13px]">
        {chips.map(([k, label, n]) => (
          <button key={k}
            className={`rounded-full px-3 py-1 font-medium ${chip === k ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
            onClick={() => setChip(k)}>
            {label} <span className="num-serif">{n}</span>
          </button>
        ))}
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}

      {list.length === 0 ? (
        <div className="mt-8">
          <EmptyState icon={Crosshair} title={chip === 'todo' ? '今天还没有可怼的靶子' : '没有符合条件的靶子'}
            desc="每天 06:00 自动提炼（宁缺毋滥，没得怼就是没得怼）；等不及点右上角「手动刷新靶子」。" />
        </div>
      ) : (
        <div className="mt-4 space-y-2">
          {list.map((t) => {
            const tgt = (t.evidence?.target || {}) as {
              sentence?: string; context?: string; heat?: string; spice?: string
            }
            const exp = expanded === t.id
            const heat = heatEvidence(t)
            const spice = SPICE[tgt.spice || ''] || null
            const scriptSt = scriptsMap[t.id]
            const open = exp ? detailOf(t.id) : null
            return (
              <div key={t.id}
                className={`rounded-2xl border bg-white transition ${exp ? 'border-emerald-300 shadow-sm' : 'border-[#E8ECF0]'}`}>
                <div className="flex cursor-pointer items-center gap-3 px-4 py-3" onClick={() => openRow(t)}>
                  {exp ? <ChevronDown size={15} className="shrink-0 text-slate-400" />
                    : <ChevronRight size={15} className="shrink-0 text-slate-400" />}
                  {heat && (
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${heat.onBoard ? 'bg-orange-100 text-orange-700' : 'bg-slate-100 text-slate-400'}`}>
                      {heat.text}
                    </span>
                  )}
                  {spice && <span className={`hidden shrink-0 rounded-full px-2 py-0.5 text-[11px] sm:inline-flex ${spice.cls}`}>{spice.label}</span>}
                  {scriptSt && (
                    <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                      {scriptSt === 'final' ? '已定稿' : scriptSt === 'failed' ? '备稿失败' : '已备稿'}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-[13.5px] font-semibold text-slate-800">{t.title}</span>
                  <span className="hidden shrink-0 text-xs text-slate-400 sm:inline">{relTime(t.created_at || '')}</span>
                </div>
                {exp && (
                  <div className="border-t border-[#E8ECF0] px-4 py-4">
                    <div className="grid gap-4 lg:grid-cols-[1fr_250px]">
                      <div className="min-w-0 space-y-3 text-[13px] leading-relaxed text-slate-700">
                        <div>
                          <span className="text-[11px] tracking-wide text-slate-400">整条视频就是这句话——三秒点名，半句话说完</span>
                          <div className="mt-1 text-[17px] font-bold leading-snug text-slate-900">{t.title}</div>
                        </div>
                        {tgt.context && (
                          <div className="rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2 text-[12.5px] text-blue-900">
                            <b>你在怼什么</b>：{tgt.context}
                          </div>
                        )}
                      </div>
                      <div className="space-y-3 text-[13px] text-slate-600">
                        {spice && (
                          <div><span className="text-[11px] text-slate-400">辣度预判</span>
                            <div className="mt-0.5"><span className={`rounded-full px-2 py-0.5 text-[11px] ${spice.cls}`}>{spice.label}</span></div>
                          </div>
                        )}
                        <div className="flex flex-col gap-2">
                          {!scriptSt && t.status === 'draft' && (
                            <button className="btn-accent btn-sm justify-center" disabled={!!busy}
                              onClick={(e) => { e.stopPropagation(); genScripts(t) }}>
                              <Clapperboard size={13} /> 备三版口播稿
                            </button>
                          )}
                          {scriptSt && scriptSt !== 'failed' && (
                            <button className="btn-sm btn justify-center"
                              onClick={(e) => { e.stopPropagation(); onNavigate('p_content') }}>
                              去内容看板定稿 →
                            </button>
                          )}
                          {t.status === 'draft' && (
                            <button className="btn-ghost btn-sm flex items-center justify-center gap-1 text-slate-500"
                              disabled={!!busy} onClick={(e) => { e.stopPropagation(); dismiss(t) }}>
                              <X size={13} /> 不怼
                            </button>
                          )}
                        </div>
                        <div className="text-[11px] leading-relaxed text-slate-400">
                          备稿默认三打法：反常识判断 / 对比打假 / 提问式。稿子进内容看板待定稿，改完拍。
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
