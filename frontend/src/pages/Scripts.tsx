import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Check,
  Clapperboard,
  FileText,
  Inbox,
  MonitorPlay,
  PenLine,
  SkipForward,
  Wand2,
} from 'lucide-react'
import { api, apiErrText, Script, waitJob } from '../api'
import {
  Busy, ChipRow, ConfirmDeleteButton, EmptyState, ErrorLine, PageHeader, QueueBar, relTime,
  sumCounts, Pager,
} from '../components'
import { useJobProgress, usePageGuard } from '../hooks'

const STATUS_BADGE: Record<string, string> = {
  generating: 'bg-sky-100 text-sky-700',
  polishing: 'bg-indigo-100 text-indigo-700',
  generated: 'bg-amber-100 text-amber-800',
  final: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
}
const STATUS_LABEL: Record<string, string> = {
  generating: '生成中',
  polishing: '打磨中',
  generated: '待定稿',
  final: '已定稿',
  failed: '生成失败',
}

interface Template {
  id: number
  name: string
  structure: { step: string; requirement: string }[]
  usage_count?: number
}

export default function Scripts({ onNavigate, accountType }: { onNavigate?: (k: string) => void; accountType?: 'persona' | 'leadgen' }) {
  const [scripts, setScripts] = useState<Script[]>([])
  const [allScripts, setAllScripts] = useState<Script[]>([])
  const [oreTitles, setOreTitles] = useState<Record<number, string>>({}) // 获客矿建稿脚本的标题映射
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [length, setLength] = useState<'short' | 'long'>('short')
  const [view, setView] = useState<'queue' | 'table'>('table')
  const [booted, setBooted] = useState(false)
  const [selVersion, setSelVersion] = useState(0)
  const [skipped, setSkipped] = useState<Set<number>>(new Set())
  const [idx, setIdx] = useState(0)
  const [session, setSession] = useState({ final: 0, skipped: 0 })
  const [status, setStatus] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [finalizedView, setFinalizedView] = useState<{ id: number; titles: { platform: string; title: string }[]; teleLen: number } | null>(null)

  // 页面守卫（hooks.ts）：错误统一 apiErrText 人话，busy 由 hook 持有并 finally 复位
  const { guard, busy, setBusy, error, setError } = usePageGuard()

  const LIMIT = 20
  const reload = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) })
    if (status) params.set('status', status)
    if (accountType) params.set('account_type', accountType) // 产线分视图：各看各的类型（服务端筛选）
    const [s, allS] = await Promise.all([
      api<{ items: Script[]; total: number; counts?: Record<string, number> }>(`/scripts?${params}`),
      api<{ items: Script[] }>('/scripts?limit=500'), // 定稿队列派生用全量
    ])
    setScripts(s.items)
    setAllScripts(allS.items)
    setTotal(s.total)
    if (s.counts) setCounts(s.counts)
    if (accountType === 'leadgen') {
      // 获客矿建稿脚本的标题映射（topics 退役后标题走 title_hint/ore 快照）
      api<{ items: { id: number; title: string }[] }>('/benchmarks?comment_kind=leadgen_ore&scan_status=all&limit=100')
        .then((d) => setOreTitles(Object.fromEntries((d.items || []).map((b) => [b.id, b.title]))))
        .catch(() => {})
    }
    return allS.items
  }, [offset, status, accountType])

  const deleteScript = (id: number) =>
    guard(async () => {
      await api(`/scripts/${id}`, { method: 'DELETE' })
      await reload()
    })

  useEffect(() => {
    reload()
      .then(() => setBooted(true))
      .catch((e) => {
        setError(apiErrText(e))
        setBooted(true)
      })
  }, [reload])

  const hasGenerating = scripts.some((s) => s.status === 'generating' || s.status === 'polishing')
  // 生成/打磨/定稿进度轮询（hooks.ts，5s 一轮，静默失败）
  const genProg = useJobProgress(['script_generate', 'script_polish', 'script_finalize'], 'script_id', hasGenerating)
  // 脚本标题：title_hint 快照（新链路）→ 获客矿映射（leadgen）→ 兜底编号
  const scriptTitle = (script: Script) =>
    script.title_hint || (script.ore_id ? (oreTitles[script.ore_id] ? `获客：${oreTitles[script.ore_id]}` : '') : '') || `脚本 #${script.id}`

  const pendingScripts = useMemo(
    () => allScripts
      .filter((s) => s.status === 'generated')
      .filter((s) => !accountType || (s.account_type ?? 'persona') === accountType),
    [allScripts, accountType],  // 2026-11-13 修复：函数体过滤的是 allScripts，依赖原写 [scripts]（靠 reload 同时 set 两者侥幸正确）
  )
  const queue = useMemo(
    () => pendingScripts.filter((s) => !skipped.has(s.id)),
    [pendingScripts, skipped],
  )
  const current = queue[idx] ?? null
  const totalAtStart = session.final + session.skipped + queue.length
  const doneCount = session.final + session.skipped

  useEffect(() => {
    setSelVersion(0)
  }, [current?.id])

  const polish = useCallback(
    (id: number) =>
      guard(async () => {
        const r = await api<{ job_id: number }>(`/scripts/${id}/polish`, { method: 'POST' })
        await waitJob(r.job_id, (j) => setBusy(`批判打磨：${j.progress}% ${j.message}`))
        const s = await api<Script>(`/scripts/${id}`)
        setScripts((list) => list.map((x) => (x.id === id ? s : x)))
      }),
    [],
  )

  const finalize = useCallback(
    (id: number, versionIndex: number) =>
      guard(async () => {
        const r = await api<{ job_id: number }>(`/scripts/${id}/finalize`, {
          method: 'POST',
          body: JSON.stringify({ version_index: versionIndex }),
        })
        await waitJob(r.job_id, (j) => setBusy(`定稿中（提词器/分镜/标题）：${j.progress}% ${j.message}`))
        const detail = await api<Script>(`/scripts/${id}`)
        setFinalizedView({
          id,
          titles: detail.title_candidates || [],
          teleLen: (detail.teleprompter_text || '').length,
        })
        setScripts((list) => list.map((x) => (x.id === id ? { ...x, status: 'final' } : x)))
        setSession((s) => ({ ...s, final: s.final + 1 }))
      }),
    [],
  )

  const nextAfterFinalize = useCallback(() => {
    if (!current) return
    setSkipped((s) => new Set(s).add(current.id))  // 让条目离队，不计入跳过数
    setFinalizedView(null)
    reload().catch(() => {})
  }, [current])

  const skip = useCallback(() => {
    if (!current) return
    setSkipped((s) => new Set(s).add(current.id))
    setSession((s) => ({ ...s, skipped: s.skipped + 1 }))
  }, [current])

  const exitQueue = () => {
    setView('table')
    setIdx(0)
    setSkipped(new Set())
    setSession({ final: 0, skipped: 0 })
  }

  // 队列键盘：1/2/3 选版本 · A 定稿 · P 打磨 · S 跳过 · Esc 退出
  useEffect(() => {
    if (view !== 'queue') return
    const onKey = (e: KeyboardEvent) => {
      const tag = (document.activeElement?.tagName || '').toUpperCase()
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (!current) {
        if (e.key === 'Escape') exitQueue()
        return
      }
      if (finalizedView && finalizedView.id === current.id) {
        if (e.key === 's' || e.key === 'S' || e.key === 'Enter') nextAfterFinalize()
        return
      }
      const n = Number(e.key)
      if (n >= 1 && n <= (current.versions?.length || 0)) setSelVersion(n - 1)
      else if (e.key === 'a' || e.key === 'A') finalize(current.id, selVersion)
      else if (e.key === 'p' || e.key === 'P') polish(current.id)
      else if (e.key === 's' || e.key === 'S') skip()
      else if (e.key === 'Escape') exitQueue()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [view, current, selVersion, finalize, polish, skip, finalizedView, nextAfterFinalize])

  if (!booted) return null

  /* ================= 定稿审阅队列 ================= */
  if (view === 'queue') {
    return (
      <div className="mx-auto max-w-5xl">
        <QueueBar label="定稿" done={doneCount + (current ? 1 : 0)} total={totalAtStart} onExit={exitQueue} exitText="退出审阅" />

        {!current ? (
          <div className="card p-10 text-center">
            <div className="text-lg font-semibold text-slate-900">本轮定稿完成 🎉</div>
            <div className="mt-2 text-sm text-slate-500">
              定稿 <b className="text-emerald-600">{session.final}</b> 个脚本 · 跳过{' '}
              <b className="text-slate-600">{session.skipped}</b> 个
            </div>
            <div className="mt-3 text-xs text-slate-400">
              定稿产物（提词器/分镜/标题）在下方表格展开可见；拍摄发布后到「今天」登记，长文走「内容工场」。
            </div>
            <div className="mt-6 flex justify-center gap-2">
              {session.skipped > 0 && pendingScripts.some((s) => skipped.has(s.id)) && (
                <button
                  onClick={() => {
                    setSkipped(new Set())
                    setIdx(0)
                    setSession({ final: 0, skipped: 0 })
                  }}
                  className="btn-ghost"
                >
                  重看跳过的 {session.skipped} 个
                </button>
              )}
              <button onClick={exitQueue} className="btn-primary">
                回到脚本库
              </button>
            </div>
          </div>
        ) : finalizedView && current && finalizedView.id === current.id ? (
          <div className="card p-8 text-center">
            <div className="text-lg font-semibold text-emerald-600">已定稿 ✓ {scriptTitle(current)}</div>
            <div className="mt-3 space-y-1.5 text-sm text-slate-600">
              {finalizedView.titles.length > 0 && (
                <div>
                  标题候选：<b className="text-slate-900">{finalizedView.titles[0]?.title}</b>
                  {finalizedView.titles.length > 1 && <span className="text-slate-400">（共 {finalizedView.titles.length} 条备选，表格展开可查）</span>}
                </div>
              )}
              <div>提词器 {finalizedView.teleLen} 字 · 分镜与字幕三件套已生成</div>
            </div>
            <div className="mt-6 flex justify-center gap-2">
              <button onClick={nextAfterFinalize} className="btn-primary">下一条 →</button>
              <button onClick={exitQueue} className="btn-ghost">回脚本库</button>
            </div>
            <div className="mt-3 text-xs text-slate-400">快捷键 S / Enter 下一条</div>
          </div>
        ) : (
          <>
            <div className="card p-6">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`badge ${STATUS_BADGE.generated}`}>三版对比</span>
                <span className="text-base font-bold text-slate-900">{scriptTitle(current)}</span>
                <span className="ml-auto text-xs text-slate-400">脚本 #{current.id}</span>
              </div>
              {current.versions?.length ? (
                <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1.7fr_1fr]">
                  {/* 主栏：选中版完整全文 */}
                  {(() => {
                    const v = current.versions[selVersion] || current.versions[0]
                    return (
                      <div className="rounded-xl border border-sky-200 bg-white p-4">
                        <div className="flex items-center gap-2">
                          <span className="badge bg-sky-100 text-sky-700">第 {selVersion + 1} 版 · {v.label}</span>
                          <span className="text-xs text-slate-400">{v.body.length} 字</span>
                        </div>
                        <div className="mt-3 rounded-lg border-l-2 border-sky-400 bg-sky-50/60 px-3 py-2 text-sm font-medium text-slate-800">
                          {v.hook}
                        </div>
                        <pre className="mt-3 max-h-[30rem] overflow-auto whitespace-pre-wrap text-[13px] leading-relaxed text-slate-700">
                          {v.body}
                        </pre>
                        {v.notes && (
                          <div className="mt-3 rounded-lg bg-violet-50 px-3 py-2 text-xs leading-relaxed text-violet-700">
                            打磨笔记：{v.notes}
                          </div>
                        )}
                      </div>
                    )
                  })()}
                  {/* 副栏：另两版（点击切换主栏） */}
                  <div className="space-y-2.5">
                    <div className="text-xs text-slate-400">切换版本（快捷键 1/2/3）</div>
                    {current.versions.map((v, i) =>
                      i === selVersion ? null : (
                        <button
                          key={i}
                          onClick={() => setSelVersion(i)}
                          className="w-full rounded-xl border border-slate-200 p-3 text-left transition hover:border-sky-300 hover:shadow-sm"
                        >
                          <div className="flex items-center gap-2">
                            <span className="grid size-5 shrink-0 place-items-center rounded-full bg-slate-200 text-[10px] font-bold text-slate-500">
                              {i + 1}
                            </span>
                            <span className="text-sm font-semibold text-slate-800">{v.label}</span>
                            <span className="ml-auto text-[11px] text-slate-400">{v.body.length} 字</span>
                          </div>
                          <div className="mt-2 line-clamp-2 text-xs leading-relaxed text-slate-500">{v.hook}</div>
                        </button>
                      )
                    )}
                  </div>
                </div>
              ) : (
                <div className="py-10 text-center text-sm text-slate-400">暂无版本</div>
              )}
            </div>

            <div className="mt-4 grid grid-cols-[1fr_0.9fr_1fr] gap-3">
              <button
                onClick={() => finalize(current.id, selVersion)}
                className="btn-success justify-center py-3 text-base"
                disabled={!current.versions?.length}
              >
                <Check size={17} /> 定稿第 {selVersion + 1} 版
              </button>
              <button onClick={() => polish(current.id)} className="btn-ghost justify-center border-slate-300 py-3 text-base">
                <Wand2 size={16} className="text-violet-500" /> 批判打磨
              </button>
              <button onClick={skip} className="btn-ghost justify-center border-slate-300 py-3 text-base">
                <SkipForward size={16} /> 跳过
              </button>
            </div>
            <div className="mt-3 text-center text-xs text-slate-400">
              快捷键：1/2/3 选版本 · A 定稿 · P 打磨 · S 跳过 · Esc 退出
            </div>
          </>
        )}
        {busy && <div className="mt-3"><Busy text={busy} /></div>}
        {error && <div className="mt-3"><ErrorLine text={error} /></div>}
      </div>
    )
  }

  /* ================= 脚本库表格 ================= */
  return (
    <div>
      <PageHeader
        icon={PenLine}
        title="脚本工场"
        desc="三版生成 → 批判打磨 → 定稿三产物（提词器 / 分镜 / 标题）"
        actions={
          <div className="flex items-center gap-2">
            {pendingScripts.length > 0 && (
              <button
                onClick={() => {
                  setView('queue')
                  setIdx(0)
                  setSkipped(new Set())
                  setSession({ final: 0, skipped: 0 })
                }}
                className="btn-accent"
              >
                定稿 {pendingScripts.length} 个脚本
              </button>
            )}
            <span className="text-xs text-slate-400">共 {scripts.length} 个</span>
          </div>
        }
      />

      <div className="mt-4">
        <ChipRow
          active={status}
          onChange={(k) => { setStatus(k); setOffset(0) }}
          chips={[
            { key: '', label: '全部', count: sumCounts(counts) },
            { key: 'generated', label: '待定稿', count: sumCounts(counts, 'generated') },
            { key: 'final', label: '已定稿', count: sumCounts(counts, 'final') },
            { key: 'failed', label: '生成失败', count: sumCounts(counts, 'failed') },
          ]}
        />
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}


      <div className="card mt-4 overflow-hidden p-0">
        {scripts.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Inbox}
              title={accountType === 'leadgen' ? '还没有获客脚本' : accountType === 'persona' ? '还没有人设脚本' : '还没有脚本'}
              desc={accountType === 'leadgen'
                ? '获客脚本从「选题 · 获客选题」的获客矿行上点「建稿」生成——获客矿=分型路由判定的需求素材。'
                : '人设脚本从「选题 · 人设靶子」的靶子卡上备稿生成（出稿=一版精品+自动打磨）。'}
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {scripts.map((s) => {
              const exp = expandedId === s.id
              return (
                <div key={s.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => setExpandedId(exp ? null : s.id)}
                    className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                  >
                    <span className="w-14 shrink-0 text-xs tabular-nums text-slate-400">#{s.id}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-slate-900">{scriptTitle(s)}</div>
                      <div className="mt-0.5 truncate text-xs text-slate-400">
                        {s.versions?.map((v) => v.label).join(' · ') || '生成中…'}
                      </div>
                    </div>
                    <span className={`badge shrink-0 ${STATUS_BADGE[s.status] || 'bg-slate-200 text-slate-600'}`}>
                      {STATUS_LABEL[s.status] || s.status}
                    </span>
                    {(s.status === 'generating' || s.status === 'polishing') && genProg[s.id] && (
                      <span className="hidden w-44 shrink-0 text-xs text-sky-600 md:inline">
                        {genProg[s.id].p}% {genProg[s.id].m}
                      </span>
                    )}
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      {relTime(s.created_at)}
                    </span>
                    {s.status === 'generated' && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setView('queue')
                          setIdx(queue.findIndex((q) => q.id === s.id) >= 0 ? queue.findIndex((q) => q.id === s.id) : 0)
                          setSkipped(new Set())
                          setSession({ final: 0, skipped: 0 })
                        }}
                        className="btn-success btn-xs shrink-0"
                      >
                        去定稿
                      </button>
                    )}
                    <ConfirmDeleteButton onConfirm={() => deleteScript(s.id)} title="删除此脚本" />
                  </div>

                  {exp && (
                    <div className="space-y-4 border-t border-sky-100 px-5 py-4">
                      {s.status !== 'final' && s.versions?.length ? (
                        <>
                          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                            {s.versions.map((v, i) => (
                              <div key={i} className="rounded-xl border border-slate-200 p-3">
                                <div className="text-sm font-semibold text-slate-800">{v.label}</div>
                                <div className="mt-2 rounded-lg border-l-2 border-sky-400 bg-sky-50/60 px-2.5 py-2 text-xs leading-relaxed font-medium text-slate-800">
                                  {v.hook}
                                </div>
                                <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-slate-50 p-2.5 text-xs leading-relaxed whitespace-pre-wrap text-slate-600">
                                  {v.body}
                                </pre>
                                {v.notes && <div className="mt-2 text-[11px] text-violet-700">{v.notes}</div>}
                              </div>
                            ))}
                          </div>
                          <div className="flex gap-2">
                            <button onClick={() => polish(s.id)} className="btn-ghost">
                              <Wand2 size={14} className="text-violet-500" /> 批判打磨
                            </button>
                          </div>
                        </>
                      ) : s.status === 'final' ? (
                        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                          <div>
                            <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
                              <MonitorPlay size={12} /> 提词器文本
                            </div>
                            <pre className="max-h-64 overflow-auto rounded-lg bg-slate-950 p-3.5 font-mono text-xs leading-loose whitespace-pre-wrap text-sky-100">
                              {s.teleprompter_text}
                            </pre>
                          </div>
                          <div className="space-y-4">
                            <div>
                              <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
                                <Clapperboard size={12} /> 分镜提示
                              </div>
                              <ul className="max-h-64 space-y-2 overflow-auto">
                                {(s.storyboard || []).map((sb, i) => (
                                  <li key={i} className="flex gap-2.5 text-[13px] leading-relaxed">
                                    <span className="grid size-5 shrink-0 place-items-center rounded-full bg-slate-900 text-[10px] font-bold text-sky-400">
                                      {i + 1}
                                    </span>
                                    <span className="text-slate-700">
                                      {sb.shot}
                                      {sb.broll_keywords?.length > 0 && (
                                        <span className="mt-1 flex flex-wrap gap-1">
                                          {sb.broll_keywords.map((k, j) => (
                                            <span key={j} className="badge bg-sky-50 text-sky-600">{k}</span>
                                          ))}
                                        </span>
                                      )}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                            <div>
                              <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
                                <FileText size={12} /> 标题候选
                              </div>
                              <ul className="space-y-1.5">
                                {(s.title_candidates || []).map((c, i) => (
                                  <li key={i} className="flex items-start gap-2 text-[13px]">
                                    <span className="badge mt-px shrink-0 bg-slate-100 text-slate-500">{c.platform}</span>
                                    <span className={c.platform === 'cover' ? 'font-bold text-slate-900' : 'text-slate-700'}>
                                      {c.title}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="py-6 text-center text-sm text-slate-400">生成中，稍等片刻后刷新</div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          <Pager total={total} limit={LIMIT} offset={offset} onPage={setOffset} />

          </div>
        )}
      </div>

    </div>
  )
}
