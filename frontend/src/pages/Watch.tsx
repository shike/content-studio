import { useCallback, useEffect, useState, useRef } from 'react'
import {
  CircleAlert, ExternalLink, Pencil, Play, Plus, Power, Radar, Scissors, Search, Trash2, Wand2,
} from 'lucide-react'
import { api, waitJob } from '../api'
import {
  Busy, ChipRow, EmptyState, ErrorLine, PageHeader, relTime, safeHref } from '../components'
import type { SectionKey } from '../App'

interface Score {
  relevance: number
  quality: number
  overall: number
}

interface WatchAccount {
  id: number
  platform: string
  name: string
  url: string
  note: string
  enabled: boolean
  kind: string
  last_scan_at: string | null
  video_total: number
  video_recent_7d: number
  scores: Score | null
}

interface ScanJob {
  id: number
  status: string
  progress: number
  message: string
  error: string | null
  created_at?: string
  result?: { queued?: number; notes?: string[] }
}

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
}

const fmtFollow = (n: number) =>
  n >= 10000
    ? `${(n / 10000).toFixed(n / 10000 >= 100 ? 0 : 1).replace(/\.0$/, '')}万`
    : String(n)

const fmtN = (n?: number) =>
  !n ? '0' : n >= 10000 ? `${(n / 10000).toFixed(1).replace(/\.0$/, '')}万` : String(n)

interface BenchmarkLite {
  id: number
  title: string
  transcript: string
  analyzed: boolean
  created_at?: string
}

const ABNORMAL_RE = /未生效|降级|失败|不存在/

export default function Watch({ onNavigate }: { onNavigate?: (k: SectionKey) => void }) {
  const [accounts, setAccounts] = useState<WatchAccount[]>([])
  const [history, setHistory] = useState<ScanJob[]>([])
  const [filter, setFilter] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [accVideos, setAccVideos] = useState<BenchmarkLite[]>([])
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState({ name: '', url: '', note: '' })
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ platform: 'douyin', name: '', url: '', note: '', kind: 'competitor' })
  const [resolveHint, setResolveHint] = useState('')
  const [addMode, setAddMode] = useState<'link' | 'search'>('link')
  const [dform, setDform] = useState({ keyword: '', direction: '' })
  const [cands, setCands] = useState<WatchCandidate[]>([])
  const [candFilter, setCandFilter] = useState('open')
  const [expCand, setExpCand] = useState<number | null>(null)
  const [discoverBusy, setDiscoverBusy] = useState('')
  const [discoverSummary, setDiscoverSummary] = useState<{
    candidates: number; new: number; already: number; skipped: number; notes: string[]
  } | null>(null)
  const [scanBusy, setScanBusy] = useState('')
  const [scanSummary, setScanSummary] = useState<{ queued: number; notes: string[] } | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const [w, h, c] = await Promise.all([
      api<{ items: WatchAccount[] }>('/watch/accounts'),
      api<{ items: ScanJob[] }>('/watch/scan/history'),
      api<{ items: WatchCandidate[] }>('/watch/discover/candidates'),
    ])
    setAccounts(w.items)
    setHistory(h.items)
    setCands(c.items)
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
  }, [reload])

  const guard = async (fn: () => Promise<void>) => {
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy('')
    }
  }

  const scanAll = () =>
    guard(async () => {
      setScanSummary(null)
      const r = await api<{ job_id: number }>('/watch/scan', { method: 'POST' })
      const job = await waitJob(r.job_id, (j) => setScanBusy(`同行扫描：${j.progress}% ${j.message}`))
      setScanBusy('')
      setScanSummary({ queued: job.result?.queued ?? 0, notes: job.result?.notes ?? [] })
      await reload()
    })

  const [scanOneId, setScanOneId] = useState<number | null>(null)
  const scanOneRef = useRef<number | null>(null)
  const scanOne = (acc: WatchAccount) =>
    guard(async () => {
      if (scanOneRef.current !== null) return  // 防双击窗口重入
      setScanSummary(null)
      scanOneRef.current = acc.id
      setScanOneId(acc.id)
      try {
        const r = await api<{ job_id: number }>(`/watch/accounts/${acc.id}/scan`, { method: 'POST' })
        const job = await waitJob(r.job_id, (j) => setScanBusy(`扫描 ${acc.name}：${j.progress}% ${j.message}`))
        setScanBusy('')
        setScanSummary({ queued: job.result?.queued ?? 0, notes: job.result?.notes ?? [] })
        await reload()
      } finally {
        scanOneRef.current = null
        setScanOneId(null)
      }
    })

  const toggle = (acc: WatchAccount) =>
    guard(async () => {
      await api(`/watch/accounts/${acc.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !acc.enabled }),
      })
      await reload()
    })

  const removeAccount = (id: number) =>
    guard(async () => {
      await api(`/watch/accounts/${id}`, { method: 'DELETE' })
      setConfirmDel(null)
      await reload()
    })

  const saveEdit = () =>
    guard(async () => {
      if (editId == null) return
      await api(`/watch/accounts/${editId}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: editForm.name,
          url: editForm.url,
          note: editForm.note,
        }),
      })
      setEditId(null)
      await reload()
    })

  const autoResolve = () =>
    guard(async () => {
      if (!form.url.trim()) return
      setResolveHint('识别中…')
      const r = await api<{ job_id: number }>('/watch/resolve', {
        method: 'POST',
        body: JSON.stringify({ url: form.url.trim() }),
      })
      const job = await waitJob(r.job_id, (j) => setResolveHint(`识别中… ${j.message}`))
      const name = (job.result?.name as string) || ''
      const url = (job.result?.url as string) || form.url
      const route = (job.result?.route as string) || ''
      setForm((f) => ({ ...f, name: name || f.name, url }))
      setResolveHint(`识别成功：${name || '（未取得昵称，请手填）'}（${route}）`)
    })

  const addAccount = () =>
    guard(async () => {
      if (!form.name.trim() || !form.url.trim()) return
      await api('/watch/accounts', {
        method: 'POST',
        body: JSON.stringify({ ...form, name: form.name.trim(), url: form.url.trim() }),
      })
      setForm({ platform: 'douyin', name: '', url: '', note: '', kind: 'competitor' })
      setResolveHint('')
      setShowAdd(false)
      await reload()
    })

  const discover = () =>
    guard(async () => {
      if (!dform.keyword.trim()) return
      setDiscoverSummary(null)
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
      } finally {
        setDiscoverBusy('')
      }
    })

  const addCand = (c: WatchCandidate) =>
    guard(async () => {
      await api(`/watch/discover/candidates/${c.id}/add`, { method: 'POST' })
      await reload()
    })

  const dismissCand = (c: WatchCandidate) =>
    guard(async () => {
      await api(`/watch/discover/candidates/${c.id}/dismiss`, { method: 'POST' })
      await reload()
    })

  const clearCands = () =>
    guard(async () => {
      await api('/watch/discover/candidates', { method: 'DELETE' })
      setExpCand(null)
      await reload()
    })

  const openExpand = (acc: WatchAccount) => {
    const next = expandedId === acc.id ? null : acc.id
    setExpandedId(next)
    setEditId(null)
    if (next != null) {
      setAccVideos([])
      api<{ items: any[] }>(`/benchmarks?limit=6&scan_status=all&author=${encodeURIComponent(acc.name)}`)
        .then((r) =>
          setAccVideos(
            r.items
              .map((b) => ({
                id: b.id,
                title: b.title || `样本 #${b.id}`,
                transcript: b.transcript || '',
                analyzed: Boolean(b.analysis && (b.analysis.hook || b.analysis.structure || b.analysis.score != null)),
                created_at: b.created_at,
              })),
          ),
        )
        .catch(() => setAccVideos([]))
    }
  }

  // 最新一次已完成扫描的 notes：判定「扫描异常」
  const lastNotes = history.find((j) => j.result?.notes)?.result?.notes ?? []
  const noteOf = (name: string) => lastNotes.find((n) => n.startsWith(`${name}：`)) || ''
  const isAbnormal = (acc: WatchAccount) => ABNORMAL_RE.test(noteOf(acc.name))

  const counts = {
    all: accounts.length,
    on: accounts.filter((a) => a.enabled).length,
    off: accounts.filter((a) => !a.enabled).length,
    never: accounts.filter((a) => !a.last_scan_at).length,
    bad: accounts.filter((a) => a.last_scan_at && isAbnormal(a)).length,
  }
  const filtered = accounts.filter((a) => {
    if (filter === 'on') return a.enabled
    if (filter === 'off') return !a.enabled
    if (filter === 'never') return !a.last_scan_at
    if (filter === 'bad') return Boolean(a.last_scan_at) && isAbnormal(a)
    return true
  })
  // 上次扫描取账号级 last_scan_at 的最大值（持久事实，不随任务清理/归档消失）；
  // 扫描任务历史只用于异常判定与展开详情
  const lastScan = accounts.reduce<string | undefined>(
    (m, a) => (a.last_scan_at && (!m || a.last_scan_at > m) ? a.last_scan_at : m),
    undefined,
  ) ?? history[0]?.created_at
  const detail = accounts.find((a) => a.id === expandedId)

  return (
    <div>
      <PageHeader
        icon={Radar}
        title="同行监测"
        desc="对标账号管理与扫描监测：扫描自动把新视频入库「待定夺」，批准后进拆解流水线"
        actions={
          <div className="flex gap-2">
            <button onClick={() => setShowAdd((v) => !v)} className="btn-ghost">
              <Plus size={14} /> 添加账号
            </button>
            <button onClick={scanAll} disabled={Boolean(scanBusy)} className="btn-accent disabled:opacity-50">
              <Play size={14} /> 扫一遍（{counts.on} 个监控中）
            </button>
          </div>
        }
      />

      {/* 状态行 */}
      <div className="card flex flex-wrap items-center gap-x-5 gap-y-1 px-5 py-3 text-[13px] text-slate-600">
        <span>监控中 <b className="text-zinc-900">{counts.on}</b></span>
        <span>已停用 <b className="text-zinc-900">{counts.off}</b></span>
        <span>上次扫描 <b className="text-zinc-900">{lastScan ? relTime(lastScan) : '从未'}</b></span>
        <span className="ml-auto text-[11px] text-slate-400">
          自动扫描；每号每次最多排 10 条新视频，视频号仅支持本地上传拆解
        </span>
      </div>

      {/* 添加账号（行内表单：贴链接识别 / 搜账号） */}
      {showAdd && (
        <div className="card mt-3 space-y-2 border-sky-200 bg-sky-50/40 p-4">
          <div className="segment">
            <button onClick={() => setAddMode('link')} className={`segment-item ${addMode === 'link' ? 'segment-item-active' : ''}`}>贴链接识别</button>
            <button onClick={() => setAddMode('search')} className={`segment-item ${addMode === 'search' ? 'segment-item-active' : ''}`}>搜账号</button>
          </div>
          {addMode === 'link' && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={form.platform}
                  onChange={(e) => setForm({ ...form, platform: e.target.value })}
                  className="input max-w-28"
                >
                  <option value="douyin">抖音</option>
                  <option value="channels">视频号</option>
                </select>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="账号名"
                  className="input max-w-44"
                />
                <input
                  value={form.url}
                  onChange={(e) => setForm({ ...form, url: e.target.value })}
                  placeholder="主页链接或任意一条视频分享链接"
                  className="input min-w-64 flex-1"
                />
                <button onClick={autoResolve} disabled={!form.url.trim() || resolveHint.includes('识别中')} className="btn-ghost btn-xs">
                  <Wand2 size={13} /> 自动识别
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={form.note}
                  onChange={(e) => setForm({ ...form, note: e.target.value })}
                  placeholder="备注（账号定位，可留空）"
                  className="input min-w-64 flex-1"
                />
                <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-600">
                  <input
                    type="checkbox"
                    checked={form.kind === 'self'}
                    onChange={(e) => setForm({ ...form, kind: e.target.checked ? 'self' : 'competitor' })}
                    className="size-3.5 accent-[#16a34a]"
                  />
                  这是我的账号（走自我风格研究，不参与同行扫描）
                </label>
                <button onClick={addAccount} disabled={!form.name.trim() || !form.url.trim()} className="btn-accent btn-xs disabled:opacity-40">
                  确认添加
                </button>
              </div>
              {resolveHint && <div className="text-xs text-sky-600">{resolveHint}</div>}
              <div className="text-[11px] text-slate-400">
                自动识别：贴视频链接自动反查（昵称+主页自动回填）；贴主页链接直接读主页标题。
              </div>
            </>
          )}
          {addMode === 'search' && (
            <div className="space-y-2">
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
                  <Search size={13} /> 开始搜索
                </button>
              </div>
              {discoverBusy && <div className="text-xs text-sky-600">{discoverBusy}</div>}
              <div className="text-[11px] text-slate-400">
                搜索引擎挖抖音热门视频 → 自动反查作者与粉丝数 → 按粉丝数出候选（约 1~3 分钟，无 LLM 消耗）；风控样本自动跳过并说明。
              </div>
            </div>
          )}
        </div>
      )}

      {/* 扫描进行中 / 结果摘要 */}
      {scanBusy && <div className="mt-3"><Busy text={scanBusy} /></div>}
      {scanSummary && !scanBusy && (
        <div className="card mt-3 border-emerald-200 bg-emerald-50/50 px-4 py-2.5 text-[13px] text-emerald-800">
          本次新入库 <b>{scanSummary.queued}</b> 条（待定夺，批准后进拆解队列）
          {scanSummary.notes.length > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-emerald-700">各账号路线/结果（展开）</summary>
              <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
                {scanSummary.notes.map((n, i) => (
                  <li key={i}>· {n}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {discoverSummary && !discoverBusy && (
        <div className="card mt-3 border-emerald-200 bg-emerald-50/50 px-4 py-2.5 text-[13px] text-emerald-800">
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
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {/* 发现的账号（关键词搜出的候选，持久化，待定夺才显示入口卡片） */}
      {cands.length > 0 && (() => {
        const cc = {
          open: cands.filter((c) => c.status === 'open').length,
          added: cands.filter((c) => c.status === 'added').length,
          dismissed: cands.filter((c) => c.status === 'dismissed').length,
        }
        const cf = candFilter ? cands.filter((c) => c.status === candFilter) : cands
        return (
          <div className="card mt-4 overflow-hidden p-0">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-zinc-900">
              <Search size={14} className="text-sky-600" /> 发现的账号
              <span className="text-xs font-normal text-slate-400">
                关键词搜出的候选对标，加入后随「扫一遍」自动监测新视频
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
                onChange={setCandFilter}
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
                {cf.map((c) => {
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
                        <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                          {relTime(c.created_at)}
                        </span>
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
                            <div>发现于 {relTime(c.created_at)}</div>
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
          </div>
        )
      })()}

      <div className="mt-4">
        <ChipRow
          active={filter}
          onChange={setFilter}
          chips={[
            { key: '', label: '全部', count: counts.all },
            { key: 'on', label: '监控中', count: counts.on },
            { key: 'off', label: '已停用', count: counts.off },
            { key: 'never', label: '从未扫描', count: counts.never },
            { key: 'bad', label: '扫描异常', count: counts.bad },
          ]}
        />
      </div>

      <div className="card mt-4 overflow-hidden p-0">
        {filtered.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Radar}
              title="还没有对标账号"
              desc="点右上角「添加账号」，贴对标账号的主页链接或任意视频分享链接，自动识别昵称与主页。"
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((acc) => {
              const exp = expandedId === acc.id
              const abnormal = Boolean(acc.last_scan_at) && isAbnormal(acc)
              return (
                <div key={acc.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => openExpand(acc)}
                    className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                  >
                    {acc.kind === 'self' && (
                      <span className="badge shrink-0 bg-sky-100 text-sky-700" title="我的账号：走「我的风格」研究链">我的</span>
                    )}
                    <span className={`badge shrink-0 ${acc.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>
                      {acc.enabled ? '监控中' : '已停用'}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-zinc-900">
                        {acc.name}
                        <span className="ml-1.5 text-xs font-normal text-slate-400">
                          {acc.platform === 'douyin' ? '抖音' : '视频号'}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-xs text-slate-400">
                        {noteOf(acc.name) || acc.note || acc.url}
                      </div>
                    </div>
                    {acc.scores ? (
                      <span className="shrink-0 text-right text-xs text-slate-500" title={`相关性 ${acc.scores.relevance} / 质量 ${acc.scores.quality}`}>
                        综合 <b className={`text-sm ${acc.scores.overall >= 7.5 ? 'text-sky-600' : 'text-slate-400'}`}>{acc.scores.overall}</b>
                      </span>
                    ) : (
                      <span className="shrink-0 text-xs text-slate-300">未评分</span>
                    )}
                    <span className="hidden w-20 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      近7天 {acc.video_recent_7d} / 共{acc.video_total}
                    </span>
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      {acc.last_scan_at ? relTime(acc.last_scan_at) : '未扫描'}
                    </span>
                    {abnormal && (
                      <span className="badge shrink-0 bg-amber-100 text-amber-800" title="上次扫描有降级或异常，展开看原因">
                        <CircleAlert size={11} /> 异常
                      </span>
                    )}
                  </div>

                  {/* 行内操作（独立于点击展开） */}
                  <div className="flex items-center gap-1 border-t border-slate-50 px-4 py-1.5">
                    <button onClick={() => scanOne(acc)} disabled={scanOneId !== null}
                      className="btn-ghost btn-xs text-slate-500 disabled:opacity-40">
                      {scanOneId === acc.id ? '扫描中…' : <><Play size={12} /> 扫此号</>}
                    </button>
                    <button onClick={() => toggle(acc)} className="btn-ghost btn-xs text-slate-500">
                      <Power size={12} /> {acc.enabled ? '停用' : '启用'}
                    </button>
                    <button
                      onClick={() => {
                        setEditId(editId === acc.id ? null : acc.id)
                        setEditForm({ name: acc.name, url: acc.url, note: acc.note })
                      }}
                      className="btn-ghost btn-xs text-slate-500"
                    >
                      <Pencil size={12} /> 编辑
                    </button>
                    {confirmDel === acc.id ? (
                      <button onClick={() => removeAccount(acc.id)} className="btn-danger btn-xs">
                        确认删除
                      </button>
                    ) : (
                      <button
                        onClick={() => {
                          setConfirmDel(acc.id)
                          setTimeout(() => setConfirmDel((c) => (c === acc.id ? null : c)), 3000)
                        }}
                        className="btn-ghost btn-xs text-slate-300 hover:text-red-500"
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                    <a
                      href={safeHref(acc.url)}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="btn-ghost btn-xs ml-auto text-slate-400"
                    >
                      <ExternalLink size={12} /> 主页
                    </a>
                  </div>

                  {/* 编辑表单 */}
                  {editId === acc.id && (
                    <div className="space-y-2 border-t border-sky-100 bg-white px-5 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <input value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} placeholder="账号名" className="input max-w-44" />
                        <input value={editForm.url} onChange={(e) => setEditForm({ ...editForm, url: e.target.value })} placeholder="主页/视频链接" className="input min-w-64 flex-1" />
                      </div>
                      <div className="flex items-center gap-2">
                        <input value={editForm.note} onChange={(e) => setEditForm({ ...editForm, note: e.target.value })} placeholder="备注" className="input flex-1" />
                        <button onClick={saveEdit} className="btn-accent btn-xs">保存</button>
                      </div>
                    </div>
                  )}

                  {/* 展开详情 */}
                  {exp && detail && (
                    <div className="space-y-4 border-t border-sky-100 px-5 py-4">
                      <div className="grid gap-4 md:grid-cols-2">
                        <div>
                          <div className="mb-1.5 text-xs font-semibold text-slate-500">账号信息</div>
                          <div className="space-y-1 text-[13px] text-slate-600">
                            <div>链接：<a className="break-all text-sky-600 hover:underline" href={safeHref(detail.url)} target="_blank" rel="noreferrer">{detail.url}</a></div>
                            <div className="whitespace-pre-wrap">{detail.note || '（无备注）'}</div>
                            {detail.scores && (
                              <div className="text-xs text-slate-400">
                                实测评分：相关性 {detail.scores.relevance} · 质量 {detail.scores.quality} · 综合 {detail.scores.overall}
                              </div>
                            )}
                          </div>
                        </div>
                        <div>
                          <div className="mb-1.5 text-xs font-semibold text-slate-500">最近扫描记录</div>
                          {history.filter((j) => noteOf(detail.name) || j.result?.notes).length === 0 ? (
                            <div className="text-[13px] text-slate-400">还没有扫描记录</div>
                          ) : (
                            <ul className="space-y-1 text-xs text-slate-600">
                              {history.slice(0, 3).map((j) => {
                                const line = (j.result?.notes ?? []).find((n) => n.startsWith(`${detail.name}：`))
                                return (
                                  <li key={j.id}>
                                    · {relTime(j.created_at)} ——{' '}
                                    {j.status !== 'succeeded'
                                      ? (j.error || j.message || j.status)
                                      : (line || (j.result?.notes ?? []).join('；').slice(0, 60) || '完成')}
                                  </li>
                                )
                              })}
                            </ul>
                          )}
                        </div>
                      </div>
                      <div>
                        <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
                          <Scissors size={12} className="text-sky-500" /> 该号最近作品（拆解库联动）
                        </div>
                        {accVideos.length === 0 ? (
                          <div className="text-[13px] text-slate-400">拆解库暂无该号作品（扫描新视频后会出现在这里）</div>
                        ) : (
                          <ul className="space-y-1">
                            {accVideos.map((v) => (
                              <li key={v.id} className="flex items-center gap-2 text-[13px]">
                                <span className={`badge shrink-0 ${v.analyzed ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-800'}`}>
                                  {v.analyzed ? '已拆解' : '待拆解'}
                                </span>
                                <span className="truncate text-slate-700">{v.title}</span>
                                <span className="ml-auto shrink-0 text-[11px] text-slate-400">{relTime(v.created_at)}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                        {onNavigate && (
                          <button onClick={() => onNavigate('benchmarks')} className="btn-ghost btn-xs mt-2">
                            去拆解库看全部 →
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
