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
  has_cookie?: boolean
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
  const [apageRaw, setApage] = useState(0)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [accVideos, setAccVideos] = useState<BenchmarkLite[]>([])
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState({ name: '', url: '', note: '', creator_cookie: '' })
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ platform: 'douyin', name: '', url: '', note: '', kind: 'competitor', account_type: 'persona' })
  const [resolveHint, setResolveHint] = useState('')
  const [scanBusy, setScanBusy] = useState('')
  const [scanSummary, setScanSummary] = useState<{ queued: number; notes: string[] } | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const [w, h] = await Promise.all([
      api<{ items: WatchAccount[] }>('/watch/accounts'),
      api<{ items: ScanJob[] }>('/watch/scan/history'),
    ])
    setAccounts(w.items)
    setHistory(h.items)
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
          ...(editForm.creator_cookie.trim()
            ? { creator_cookie: editForm.creator_cookie.trim() } : {}),
        }),
      })
      setEditId(null)
      await reload()
    })

  const clearCookie = (id: number) =>
    guard(async () => {
      await api(`/watch/accounts/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ creator_cookie: '' }),
      })
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
      setForm({ platform: 'douyin', name: '', url: '', note: '', kind: 'competitor', account_type: 'persona' })
      setResolveHint('')
      setShowAdd(false)
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
  const APAGE = 20
  const apages = Math.max(1, Math.ceil(filtered.length / APAGE))
  const apage = Math.min(apageRaw, apages - 1)
  const pagedAccounts = filtered.slice(apage * APAGE, (apage + 1) * APAGE)
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
        title="监控的账号"
        desc="对标账号管理与扫描监测：扫描自动把新视频入库拆解库「待定夺」，批准后进拆解流水线"
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
                <div className="segment">
                  <button onClick={() => setForm({ ...form, account_type: 'persona' })} className={`segment-item ${(form.account_type || 'persona') === 'persona' ? 'segment-item-active' : ''}`}>人设号</button>
                  <button onClick={() => setForm({ ...form, account_type: 'leadgen' })} className={`segment-item ${form.account_type === 'leadgen' ? 'segment-item-active' : ''}`}>获客号</button>
                </div>
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
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      <div className="mt-4">
        <ChipRow
          active={filter}
          onChange={(k) => { setFilter(k); setApage(0) }}
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
            {pagedAccounts.map((acc) => {
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
                    {acc.kind === 'self' && acc.has_cookie && (
                      <span className="badge shrink-0 bg-emerald-100 text-emerald-700" title="已配创作者中心登录态：扫描走全量抓取，含播放量">播放量已配</span>
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
                        setEditForm({ name: acc.name, url: acc.url, note: acc.note, creator_cookie: '' })
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
                      {acc.kind === 'self' && (
                        <div className="flex items-center gap-2">
                          <input type="password" value={editForm.creator_cookie} onChange={(e) => setEditForm({ ...editForm, creator_cookie: e.target.value })} placeholder={acc.has_cookie ? '已配置创作者中心 Cookie（粘贴新值=更换，留空=不变）' : '创作者中心 Cookie：creator.douyin.com 登录后 F12 → Network → 复制整串 Cookie'} className="input min-w-72 flex-1 font-mono text-xs" />
                          {acc.has_cookie && (
                            <button onClick={() => clearCookie(acc.id)} className="btn btn-xs">清除</button>
                          )}
                        </div>
                      )}
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
      {filtered.length > APAGE && (
        <div className="border-t border-slate-100 px-4 py-2.5 text-center text-[13px] text-slate-500">
          <button onClick={() => setApage(Math.max(0, apage - 1))} disabled={apage === 0}
            className="btn-ghost btn-xs disabled:opacity-40">← 上一页</button>
          <span className="mx-3">第 {apage * APAGE + 1}–{Math.min((apage + 1) * APAGE, filtered.length)} 条 · 共 {filtered.length} 条</span>
          <button onClick={() => setApage(Math.min(apages - 1, apage + 1))} disabled={apage >= apages - 1}
            className="btn-ghost btn-xs disabled:opacity-40">下一页 →</button>
        </div>
      )}
    </div>
  )
}

