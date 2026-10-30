import { useCallback, useEffect, useState } from 'react'
import { Plus, RefreshCw, Search, Trash2 } from 'lucide-react'
import { api, waitJob } from '../api'
import { Busy, ChipRow, EmptyState, relTime, safeHref } from '../components'

interface WatchCandidate {
  id: number
  keyword: string
  direction: string
  name: string
  url: string
  signature: string
  follower_count: number
  sample_video_id: string
  sample_title: string
  status: string
  created_at: string
  stats: { digg?: number; comment?: number; share?: number; collect?: number }
  account_type?: string
  leadgen_density?: number
}

const fmtFollow = (n: number) =>
  n >= 10000
    ? `${(n / 10000).toFixed(n / 10000 >= 100 ? 0 : 1).replace(/\.0$/, '')}万`
    : String(n)

const fmtN = (n?: number) =>
  !n ? '0' : n >= 10000 ? `${(n / 10000).toFixed(1).replace(/\.0$/, '')}万` : String(n)

/** 发现的账号（自「监测账号」页拆出，2026-10-30）：关键词挖出的候选对标号，定夺后进监控清单。 */
export default function DiscoveredAccounts() {
  const [cands, setCands] = useState<WatchCandidate[]>([])
  const [candFilter, setCandFilter] = useState('open')
  const [cpageRaw, setCpage] = useState(0)
  const [expCand, setExpCand] = useState<number | null>(null)
  const [dform, setDform] = useState({ keyword: '', direction: '' })
  const [discoverBusy, setDiscoverBusy] = useState('')
  const [discoverSummary, setDiscoverSummary] = useState<{
    candidates: number; new: number; already: number; skipped: number; notes: string[]
  } | null>(null)
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const r = await api<{ items: WatchCandidate[] }>('/watch/discover/candidates')
    setCands(r.items)
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
  }, [reload])

  const discover = () =>
    (async () => {
      if (!dform.keyword.trim()) return
      setDiscoverSummary(null)
      setError('')
      try {
        const r = await api<{ job_id: number }>('/watch/discover', {
          method: 'POST',
          body: JSON.stringify({ keyword: dform.keyword.trim(), direction: dform.direction.trim() }),
        })
        const job = await waitJob(r.job_id, (j) => setDiscoverBusy(`搜账号：${j.progress}% ${j.message}`))
        setDiscoverSummary({
          candidates: job.result?.candidates ?? 0,
          new: job.result?.new ?? 0,
          already: job.result?.already ?? 0,
          skipped: job.result?.skipped ?? 0,
          notes: job.result?.notes ?? [],
        })
        setCandFilter('open')
        await reload()
      } catch (e) {
        setError(String(e))
      } finally {
        setDiscoverBusy('')
      }
    })()

  const addCand = (c: WatchCandidate) =>
    (async () => {
      setError('')
      try {
        await api(`/watch/discover/candidates/${c.id}/add`, { method: 'POST' })
        await reload()
      } catch (e) {
        setError(String(e))
      }
    })()

  const dismissCand = (c: WatchCandidate) =>
    (async () => {
      setError('')
      try {
        await api(`/watch/discover/candidates/${c.id}/dismiss`, { method: 'POST' })
        await reload()
      } catch (e) {
        setError(String(e))
      }
    })()

  const clearCands = () =>
    (async () => {
      setError('')
      try {
        await api('/watch/discover/candidates', { method: 'DELETE' })
        setExpCand(null)
        await reload()
      } catch (e) {
        setError(String(e))
      }
    })()

  const cc = {
    open: cands.filter((c) => c.status === 'open').length,
    added: cands.filter((c) => c.status === 'added').length,
    dismissed: cands.filter((c) => c.status === 'dismissed').length,
  }
  const cf = candFilter ? cands.filter((c) => c.status === candFilter) : cands
  const CPAGE = 20
  const cpages = Math.max(1, Math.ceil(cf.length / CPAGE))
  const cpage = Math.min(cpageRaw, cpages - 1)
  const pagedC = cf.slice(cpage * CPAGE, (cpage + 1) * CPAGE)

  return (
    <div>
      {/* 手动挖掘：关键词搜账号 */}
      <div className="card space-y-2 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={dform.keyword}
            onChange={(e) => setDform({ ...dform, keyword: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && discover()}
            placeholder="关键词，如：企业数字化转型"
            className="input min-w-48 flex-1"
          />
          <input
            value={dform.direction}
            onChange={(e) => setDform({ ...dform, direction: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && discover()}
            placeholder="方向补充（选填），如：企业管理 口播"
            className="input min-w-56 flex-1"
          />
          <button onClick={discover} disabled={!dform.keyword.trim() || discoverBusy !== ''} className="btn-accent btn-xs disabled:opacity-40">
            {discoverBusy ? <RefreshCw size={13} className="animate-spin" /> : <Search size={13} />} 开始搜索
          </button>
          <span className="text-[11px] text-slate-400">
            搜索引擎挖抖音热门视频 → 自动反查作者与粉丝数 → 按粉丝数出候选（约 1~3 分钟，无 LLM 消耗）；风控样本自动跳过并说明。
          </span>
        </div>
        {discoverBusy && <div className="text-xs text-sky-600">{discoverBusy}</div>}
        {discoverSummary && !discoverBusy && (
          <div className="card border-emerald-200 bg-emerald-50/50 px-4 py-2.5 text-[13px] text-emerald-800">
            发现 <b>{discoverSummary.candidates}</b> 个候选账号：新增 {discoverSummary.new}，
            已在清单 {discoverSummary.already}
            {discoverSummary.skipped > 0 && <>，风控跳过 {discoverSummary.skipped}</>}
            {discoverSummary.notes.length > 0 && (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-emerald-700">检索与解析明细（展开）</summary>
                <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
                  {discoverSummary.notes.map((n, i) => (
                    <li key={i}>· {n}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
        {error && <div className="text-xs text-red-600">{error}</div>}
      </div>

      {/* 候选清单 */}
      <div className="card mt-4 overflow-hidden p-0">
        {cands.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Search}
              title="还没有候选账号"
              desc="用上面的关键词开始搜索，挖出的对标号会出现在这里；定夺「监测」后进监控清单。"
            />
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-zinc-900">
              <Search size={14} className="text-sky-600" /> 发现的账号
              <span className="text-xs font-normal text-slate-400">
                加入后随「扫一遍」自动监测新视频
              </span>
              {(cc.added + cc.dismissed) > 0 && (
                <button onClick={clearCands} className="btn-ghost btn-xs ml-auto text-slate-400">
                  <Trash2 size={12} /> 清掉已处理
                </button>
              )}
            </div>
            <div className="px-4 pt-3">
              <ChipRow
                active={candFilter}
                onChange={(k) => { setCandFilter(k); setCpage(0) }}
                chips={[
                  { key: 'open', label: '待定夺', count: cc.open },
                  { key: 'added', label: '已加入', count: cc.added },
                  { key: 'dismissed', label: '已忽略', count: cc.dismissed },
                  { key: '', label: '全部', count: cands.length },
                ]}
              />
            </div>
            {cf.length === 0 ? (
              <div className="px-4 py-4 text-[13px] text-slate-400">该状态下暂无候选。</div>
            ) : (
              <div className="divide-y divide-slate-100">
                {pagedC.map((c) => {
                  const expC = expCand === c.id
                  return (
                    <div key={c.id} className={expC ? 'bg-sky-50/40' : ''}>
                      <div
                        onClick={() => setExpCand(expC ? null : c.id)}
                        className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                      >
                        <span className={`badge shrink-0 ${c.status === 'added' ? 'bg-emerald-100 text-emerald-700' : c.status === 'dismissed' ? 'bg-slate-200 text-slate-500' : 'bg-amber-100 text-amber-800'}`}>
                          {c.status === 'added' ? '已加入' : c.status === 'dismissed' ? '已忽略' : '待定夺'}
                        </span>
                        <span className="w-16 shrink-0 text-right text-sm font-bold tabular-nums text-sky-600" title="粉丝数（热门排序键）">
                          {fmtFollow(c.follower_count)}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium text-zinc-900">{c.name}</div>
                          <div className="mt-0.5 truncate text-xs text-slate-400">
                            样本：{c.sample_title || c.sample_video_id}
                            {c.created_at ? ` · 发现于 ${relTime(c.created_at)}` : ''}
                          </div>
                        </div>
                        {c.status === 'open' && (
                          <span className="flex shrink-0 items-center gap-1.5">
                            <button onClick={(e) => { e.stopPropagation(); addCand(c) }} className="btn-accent btn-xs">
                              <Plus size={12} /> 监测
                            </button>
                            <button onClick={(e) => { e.stopPropagation(); dismissCand(c) }} className="btn-ghost btn-xs text-slate-400">
                              忽略
                            </button>
                          </span>
                        )}
                        {c.account_type === 'leadgen' && (
                          <span className="badge shrink-0 bg-amber-100 text-amber-800" title="评论区求带密度≥8% 判获客型">
                            获客 · 求带 {Math.round((c.leadgen_density ?? 0) * 100)}%
                          </span>
                        )}
                      </div>
                      {expC && (
                        <div className="grid gap-5 border-t border-sky-100 px-5 py-4 text-[13px] md:grid-cols-2">
                          <div>
                            <div className="mb-1.5 text-xs font-semibold text-slate-500">账号简介</div>
                            <div className="whitespace-pre-wrap leading-relaxed text-slate-600">
                              {c.signature || '（无签名）'}
                            </div>
                            <div className="mt-3">
                              <div className="mb-1 text-xs font-semibold text-slate-500">样本视频</div>
                              <a
                                href={`https://www.douyin.com/video/${c.sample_video_id}`}
                                target="_blank"
                                rel="noreferrer"
                                className="text-sky-600 hover:underline"
                              >
                                {c.sample_title || c.sample_video_id}
                              </a>
                              <div className="mt-1 text-xs text-slate-400">
                                赞 {fmtN(c.stats.digg)} · 评 {fmtN(c.stats.comment)} · 转 {fmtN(c.stats.share)} · 藏 {fmtN(c.stats.collect)}
                              </div>
                            </div>
                          </div>
                          <div className="space-y-1 text-slate-600">
                            <div className="mb-1.5 text-xs font-semibold text-slate-500">来源与状态</div>
                            <div>发现关键词：{c.keyword}{c.direction ? ` · ${c.direction}` : ''}</div>
                            <div>粉丝：{fmtFollow(c.follower_count)}（按此排序）</div>
                            <div className="break-all">
                              主页：<a className="text-sky-600 hover:underline" href={safeHref(c.url)} target="_blank" rel="noreferrer">{c.url}</a>
                            </div>
                            {c.status === 'open' && (
                              <div className="pt-1.5">
                                <button onClick={() => addCand(c)} className="btn-accent btn-xs">
                                  <Plus size={12} /> 加入监测清单
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}
      </div>
      {cf.length > CPAGE && (
        <div className="mt-3 flex items-center justify-center gap-3 text-[13px] text-slate-500">
          <button onClick={() => setCpage(Math.max(0, cpage - 1))} disabled={cpage === 0}
            className="btn-ghost btn-xs disabled:opacity-40">← 上一页</button>
          <span>第 {cpage * CPAGE + 1}–{Math.min((cpage + 1) * CPAGE, cf.length)} 条 · 共 {cf.length} 条</span>
          <button onClick={() => setCpage(Math.min(cpages - 1, cpage + 1))} disabled={cpage >= cpages - 1}
            className="btn-ghost btn-xs disabled:opacity-40">下一页 →</button>
        </div>
      )}
    </div>
  )
}

