import { useCallback, useEffect, useState } from 'react'
import { Inbox, Link2, Package, Plus, Radar, RefreshCw } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, PageHeader, relTime } from '../components'
import { Chips, Toolbar } from './MaterialsShared'
import KbTab from './KbTab'
import MaterialsPool from './MaterialsPool'

/**
 * 素材库（PRD R1）：进料口的源 / 池 / 库三层（2026-11-18 用户定版三 tab）：
 * 监控清单=采集源与候选账号；素材池=待定夺/可用/已忽略与贴链拆解；
 * 知识库=全库内容榜（每日推荐视图在「今天」页，两处分工）。
 * 层次法则：tab=实体（源/素材/知识文档），chips=生命周期，徽章=待办。
 */

interface SourceRow {
  id: number
  kind: string
  platform: string
  name: string
  handle: string
  url: string
  status: string
  config: Record<string, any>
  last_sync_at: string | null
  note: string
  enabled?: boolean
  account_type?: string
  follower_count?: number
  signature?: string
  dismissed?: boolean
  video_total?: number
  video_recent_7d?: number
}

/* ─────────────────────────── Tab ② 监控清单 ─────────────────────────── */

type WatchChip = 'cand' | 'active' | 'disabled'

/** 监控清单：同行账号/话题雷达 + 候选账号定夺——源的完整生命周期一处管。 */
function WatchTab() {
  const [kind, setKind] = useState<'peer_account' | 'radar_topic'>('peer_account')
  const [chip, setChip] = useState<WatchChip>('active')
  const [rows, setRows] = useState<SourceRow[]>([])
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({ name: '', url: '', ch_id: '', account_type: 'persona' })
  const [resolveUrl, setResolveUrl] = useState('')
  const [kw, setKw] = useState('')
  const [dir, setDir] = useState('')
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    api<{ items: SourceRow[] }>(`/sources?kind=${kind}`)
      .then((d) => { setRows(d.items || []); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [kind])
  useEffect(() => { load() }, [load])

  // 客户端分态：候选=status=candidate 且未忽略；监控中=enabled；其余=已停用
  const cands = rows.filter((r) => r.status === 'candidate' && !r.dismissed)
  const active = rows.filter((r) => r.enabled && r.status !== 'candidate')
  const disabled = rows.filter((r) => !r.enabled && r.status !== 'candidate')
  const dismissedN = rows.filter((r) => r.status === 'candidate' && r.dismissed).length
  const shown = chip === 'cand' ? cands : chip === 'active' ? active : disabled

  const runJob = async (submit: () => Promise<{ job_id: number }>, label: string, reload = false) => {
    setError('')
    try {
      const r = await submit()
      setMsg(`${label}任务 #${r.job_id} 已提交`)
      waitJob(r.job_id, (j) => setMsg(`${label}：${j.progress}% ${j.message || ''}`))
        .then(() => { setMsg(`${label}完成`); if (reload) load() })
        .catch((e) => { setError(`${label}失败：${apiErrText(e)}`); setMsg('') })
    } catch (e) { setError(apiErrText(e)) }
  }

  const add = async () => {
    setError('')
    try {
      if (kind === 'radar_topic') {
        await api('/sources', { method: 'POST', body: JSON.stringify({ kind: 'radar_topic', name: form.name, ext_id: form.ch_id }) })
      } else {
        await api('/sources', { method: 'POST', body: JSON.stringify({ kind: 'peer_account', name: form.name, url: form.url, account_type: form.account_type }) })
      }
      setForm({ name: '', url: '', ch_id: '', account_type: 'persona' })
      setMsg('已添加采集源')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const toggle = async (r: SourceRow) => {
    try {
      await api(`/sources/${r.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !r.enabled }) })
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const extractTopic = async () => {
    setError('')
    try {
      const r = await api<{ items: { ch_id: string; name: string }[] }>('/sources/radar-extract', {
        method: 'POST', body: JSON.stringify({ url: resolveUrl }),
      })
      if (r.items?.length) {
        setForm((f) => ({ ...f, name: r.items[0].name, ch_id: r.items[0].ch_id }))
        setMsg(`已提取话题：#${r.items[0].name}（${r.items[0].ch_id}）`)
      } else setMsg('该视频未提取到话题标签')
    } catch (e) { setError(apiErrText(e)) }
  }

  const resolve = async () => {
    setError('')
    try {
      const r = await api<{ job_id: number }>('/sources/resolve', {
        method: 'POST', body: JSON.stringify({ url: resolveUrl }),
      })
      const info = await waitJob(r.job_id, (j) => setMsg(`识别：${j.progress}% ${j.message || ''}`))
      setForm((f) => ({ ...f, name: (info.result as any)?.name || f.name, url: (info.result as any)?.url || f.url }))
      setMsg(`识别成功：${(info.result as any)?.name}——确认后点「添加」`)
    } catch (e) { setError(`识别失败：${apiErrText(e)}`); setMsg('') }
  }

  const decide = async (r: SourceRow, action: 'activate' | 'dismiss') => {
    setError('')
    try {
      const res = await api<{ duplicate?: boolean }>(`/sources/${r.id}/${action}`, { method: 'POST' })
      setMsg(action === 'activate'
        ? (res.duplicate ? '该账号已在清单（候选已标已处理）' : `已加入采集清单：${r.name}`)
        : '已忽略')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const backfillLabel = kind === 'peer_account' ? '同行存量回填' : '话题存量回填'

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-1.5">
          {[['peer_account', '同行账号'], ['radar_topic', '话题雷达']].map(([v, label]) => (
            <button key={v} onClick={() => setKind(v as 'peer_account' | 'radar_topic')}
              className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${kind === v ? 'bg-[#0a2f24] text-[#f0e9d5]' : 'text-slate-500 hover:bg-slate-100'}`}>
              {label}
            </button>
          ))}
        </div>
        <Chips
          items={[
            ...(kind === 'peer_account' ? [{ v: 'cand', label: `待定夺 ${cands.length}` }] : []),
            { v: 'active', label: `监控中 ${active.length}` },
            { v: 'disabled', label: `已停用 ${disabled.length}` },
          ]}
          value={chip} onPick={(v) => setChip(v as WatchChip)} />
      </div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />

      {chip === 'cand' ? (
        <>
          <Toolbar
            left={<>
              <input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="关键词（如 FDE、企业AI落地）"
                className="w-52 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="方向（可选）"
                className="w-28 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
            </>}
            actions={<>
              <button onClick={() => runJob(() => api('/sources/discover', { method: 'POST', body: JSON.stringify({ keyword: kw, direction: dir }) }), '搜账号')}
                className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                搜账号
              </button>
              <button onClick={() => runJob(() => api('/sources/hl-search', { method: 'POST' }), '同城矩阵')}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                同城矩阵补跑
              </button>
              <button onClick={() => api('/sources', { method: 'DELETE' }).then((r: any) => { setMsg(`已清 ${r.deleted} 条已处理候选`); load() })}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                清已忽略{dismissedN > 0 ? ` ${dismissedN}` : ''}
              </button>
            </>} />
          {!loaded ? null : cands.length === 0 ? (
            <EmptyState icon={Inbox} title="没有待定夺的候选账号" desc="用上面的「搜账号」关键词发现，或每晚同城矩阵自动挖" />
          ) : (
            <div className="space-y-2">
              {cands.map((r) => {
                const at = (r.config?.account_type || 'persona') as string
                const fans = r.config?.follower_count || r.follower_count || 0
                return (
                  <div key={r.id} className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${at === 'leadgen' ? 'bg-violet-100 text-violet-700' : 'bg-emerald-100 text-emerald-700'}`}>
                        {at === 'leadgen' ? '获客' : '人设'}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{r.name}</span>
                      {fans > 0 && <span className="shrink-0 text-[11px] text-slate-400">{fans >= 10000 ? `${(fans / 10000).toFixed(1)}万` : fans} 粉</span>}
                      <span className="shrink-0 text-[11px] text-slate-400">
                        {r.config?.keyword === '搜索矩阵' ? '同城矩阵' : `搜「${r.config?.keyword || '—'}」`}
                      </span>
                      <button onClick={() => decide(r, 'activate')}
                        className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                        加入
                      </button>
                      <button onClick={() => decide(r, 'dismiss')}
                        className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-500 transition hover:bg-slate-50">
                        忽略
                      </button>
                    </div>
                    {(r.signature || r.config?.pure_reason) && (
                      <div className="mt-1.5 line-clamp-2 text-[12.5px] text-slate-500">
                        {r.signature}{r.config?.pure_reason ? `｜${r.config.pure_reason}` : ''}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      ) : (
        <>
          <Toolbar
            left={<span className="text-[13px] text-slate-600">
              {kind === 'peer_account'
                ? `启用 ${active.length} / ${active.length + disabled.length} 个同行采集源`
                : `监控 ${active.length} 个话题（每日 6 点随选题工场巡检）`}
            </span>}
            actions={<>
              <button onClick={() => runJob(() => api('/sources/sync-all', { method: 'POST' }), '全量扫描', true)}
                className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                <RefreshCw size={13} className="inline" /> 全量扫描
              </button>
              <button onClick={() => runJob(() => api(kind === 'peer_account' ? '/materials/backfill' : '/sources/backfill-topics',
                { method: 'POST', body: JSON.stringify({ scope: 'peer' }) }), backfillLabel)}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                {backfillLabel}
              </button>
            </>} />
          <div className="mb-4 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
            <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">
              {kind === 'peer_account' ? '添加同行账号（贴主页或视频链接）' : '添加话题（贴视频链接可提取 ch_id）'}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input value={resolveUrl} onChange={(e) => setResolveUrl(e.target.value)} placeholder="粘贴分享链接/口令全文"
                className="w-64 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              {kind === 'peer_account' ? (
                <button onClick={resolve}
                  className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                  <Link2 size={13} className="inline" /> 贴链识别
                </button>
              ) : (
                <button onClick={extractTopic}
                  className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                  提取话题
                </button>
              )}
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="名称"
                className="w-36 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              {kind === 'peer_account' ? (
                <>
                  <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="主页链接"
                    className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                  <select value={form.account_type} onChange={(e) => setForm({ ...form, account_type: e.target.value })}
                    className="rounded-lg border border-[#E8ECF0] px-2 py-1.5 text-[13px]">
                    <option value="persona">人设号</option>
                    <option value="leadgen">获客号</option>
                  </select>
                </>
              ) : (
                <input value={form.ch_id} onChange={(e) => setForm({ ...form, ch_id: e.target.value })} placeholder="ch_id（数字）"
                  className="w-32 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              )}
              <button onClick={add}
                className="rounded-full bg-[#0a2f24] px-3 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90">
                <Plus size={13} className="inline" /> 添加
              </button>
            </div>
          </div>
          {!loaded ? null : shown.length === 0 ? (
            <EmptyState icon={Radar} title={chip === 'active' ? '还没有监控中的源' : '没有已停用的源'}
              desc={chip === 'active' ? (kind === 'peer_account' ? '贴同行主页链接添加；或切到「待定夺」看看候选' : '贴一条带话题的视频链接提取 ch_id 后添加') : '停用的源出现在这里，可随时启用'} />
          ) : (
            <div className="space-y-2">
              {shown.map((r) => (
                <div key={r.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                  {kind === 'peer_account' && r.account_type && (
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${r.account_type === 'leadgen' ? 'bg-violet-50 text-violet-700' : 'bg-emerald-50 text-emerald-700'}`}>
                      {r.account_type === 'leadgen' ? '获客号' : '人设号'}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{r.name}</span>
                  {kind === 'peer_account' && (
                    <span className="shrink-0 text-[11px] text-slate-400">
                      素材 {r.video_total ?? 0} 条（7 天 {r.video_recent_7d ?? 0}）
                    </span>
                  )}
                  <span className="shrink-0 text-[11px] text-slate-400">
                    上次同步 {r.last_sync_at ? relTime(r.last_sync_at) : '—'}
                  </span>
                  <button onClick={() => runJob(() => api(`/sources/${r.id}/sync`, { method: 'POST' }), `扫描 ${r.name}`, true)}
                    className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                    扫描
                  </button>
                  <button onClick={() => toggle(r)}
                    className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                    {r.enabled ? '停用' : '启用'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

type Tab = 'watch' | 'pool' | 'kb'

/** 旧 tab 键 → 三 tab 兼容映射（收件箱/全部素材=素材池；采集源/候选=监控清单）。 */
function normalizeTab(tabId?: string): Tab {
  if (tabId === 'kb') return 'kb'
  if (tabId === 'pool' || tabId === 'topics' || tabId === 'inbox' || tabId === 'all') return 'pool'
  return 'watch' // sources / cand / 未知 → 监控清单
}

export default function Materials({ onNavigate, tabId, onTabChange }: {
  onNavigate?: (k: string) => void; tabId?: string; onTabChange?: (t: string) => void
} = {}) {
  const [tab, setTab] = useState<Tab>(normalizeTab(tabId))

  const pick = (t: Tab) => { setTab(t); onTabChange?.(t) }
  const tabs: { id: Tab; label: string }[] = [
    { id: 'watch', label: '监控清单' },
    { id: 'pool', label: '素材池' },
    { id: 'kb', label: '知识库' },
  ]

  return (
    <div>
      <PageHeader icon={Package} title="素材库"
        desc="进料口的源 / 池 / 库：配监控号与话题雷达、定夺素材、回看全库文档——每日取料（热门评论/知识库推荐）在「今天」页。" />
      <div className="mb-5 flex items-center gap-1.5 border-b border-[#E8ECF0] pb-2.5">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => pick(t.id)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${tab === t.id ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'watch' && <WatchTab />}
      {tab === 'pool' && <MaterialsPool />}
      {tab === 'kb' && <KbTab onNavigate={onNavigate} />}
    </div>
  )
}
