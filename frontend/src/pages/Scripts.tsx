import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Check,
  Clapperboard,
  FileText,
  Inbox,
  MonitorPlay,
  PenLine,
  SkipForward,
  Trash2,
  Wand2,
} from 'lucide-react'
import { api, apiErrText, AUDIENCE_LABELS, Script, SOURCE_LABELS, Topic, waitJob } from '../api'
import type { SectionKey } from '../App'
import { Busy, ChipRow, EmptyState, ErrorLine, PageHeader, QueueBar, relTime, Pager } from '../components'
import { DetailBody } from '../topicDetail'

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

export default function Scripts({ onNavigate }: { onNavigate?: (k: SectionKey) => void }) {
  const [scripts, setScripts] = useState<Script[]>([])
  const [allScripts, setAllScripts] = useState<Script[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [topics, setTopics] = useState<Topic[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [tplName, setTplName] = useState('')
  const [tplStructure, setTplStructure] = useState('')
  const [topicId, setTopicId] = useState<number | null>(null)
  const [length, setLength] = useState<'short' | 'long'>('short')
  const [view, setView] = useState<'queue' | 'table'>('table')
  const [booted, setBooted] = useState(false)
  const [selVersion, setSelVersion] = useState(0)
  const [skipped, setSkipped] = useState<Set<number>>(new Set())
  const [idx, setIdx] = useState(0)
  const [session, setSession] = useState({ final: 0, skipped: 0 })
  const [status, setStatus] = useState('')
  const [writingFilter, setWritingFilter] = useState(false)
  const [topicOrder, setTopicOrder] = useState<'score' | 'new'>('score')
  const [expandedTopicId, setExpandedTopicId] = useState<number | null>(null)
  const [topicDetail, setTopicDetail] = useState<Record<number, Topic>>({})
  const [topicGen, setTopicGen] = useState<Record<number, { p: number; m: string }>>({})
  const [topicGenErr, setTopicGenErr] = useState<Record<number, string>>({})
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [useStyle, setUseStyle] = useState(true)
  const [finalizedView, setFinalizedView] = useState<{ id: number; titles: { platform: string; title: string }[]; teleLen: number } | null>(null)
  const [genProg, setGenProg] = useState<Record<number, { p: number; m: string }>>({})

  const LIMIT = 20
  const reload = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) })
    if (status) params.set('status', status)
    const [s, allS, t, tpl] = await Promise.all([
      api<{ items: Script[]; total: number; counts?: Record<string, number> }>(`/scripts?${params}`),
      api<{ items: Script[] }>('/scripts?limit=500'), // 队列与"待写选题"派生用全量
      api<{ items: Topic[] }>('/topics?limit=200'),
      api<{ items: Template[] }>('/scripts/templates'),
    ])
    setScripts(s.items)
    setAllScripts(allS.items)
    setTotal(s.total)
    if (s.counts) setCounts(s.counts)
    setTopics(t.items)
    setTemplates(tpl.items)
    return allS.items
  }, [offset, status])

  const deleteScript = (id: number) =>
    guard(async () => {
      await api(`/scripts/${id}`, { method: 'DELETE' })
      setConfirmDel(null)
      await reload()
    })

  const createTemplate = () =>
    guard(async () => {
      const structure = tplStructure
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const m = line.split(/[：:]/)
          return { step: (m[0] || '').trim(), requirement: (m.slice(1).join('：') || '').trim() }
        })
        .filter((x) => x.step)
      if (!tplName.trim() || structure.length === 0) {
        setError(new Error('模板名和至少一行「步骤：要求」都要填').toString().replace(/^Error:\s*/, ''))
        return
      }
      await api('/scripts/templates', {
        method: 'POST',
        body: JSON.stringify({ name: tplName.trim(), structure }),
      })
      setTplName('')
      setTplStructure('')
      await reload()
    })

  const deleteTemplate = (id: number) =>
    guard(async () => {
      await api(`/scripts/templates/${id}`, { method: 'DELETE' })
      await reload()
    })

  useEffect(() => {
    reload()
      .then(() => setBooted(true))
      .catch((e) => {
        setError(String(e))
        setBooted(true)
      })
  }, [reload])

  const topicMap = useMemo(() => new Map(topics.map((t) => [t.id, t])), [topics])

  const hasGenerating = scripts.some((s) => s.status === 'generating' || s.status === 'polishing')
  useEffect(() => {
    if (!hasGenerating) return
    let stop = false
    const tick = async () => {
      try {
        const d = await api<{ items: { type: string; status: string; progress: number; message: string; payload: Record<string, any> }[] }>('/jobs?limit=50')
        const map: Record<number, { p: number; m: string }> = {}
        for (const j of d.items || []) {
          if (j.status !== 'running' && j.status !== 'queued') continue
          const sid = j.payload?.script_id
          if (!sid || !['script_generate', 'script_polish', 'script_finalize'].includes(j.type)) continue
          map[sid] = { p: j.progress || 0, m: j.message || '' }
        }
        if (!stop) setGenProg(map)
      } catch { /* 静默，下轮再取 */ }
    }
    tick()
    const t = setInterval(tick, 5000)
    return () => { stop = true; clearInterval(t) }
  }, [hasGenerating])
  const topicTitle = (script: Script) =>
    (script.topic_id ? topicMap.get(script.topic_id)?.title : '') || `脚本 #${script.id}`

  const pendingScripts = useMemo(
    () => allScripts.filter((s) => s.status === 'generated'),
    [scripts],
  )
  const queue = useMemo(
    () => pendingScripts.filter((s) => !skipped.has(s.id)),
    [pendingScripts, skipped],
  )
  const current = queue[idx] ?? null
  const totalAtStart = session.final + session.skipped + queue.length
  const doneCount = session.final + session.skipped

  const filtered = scripts // 服务端已筛选分页
  const statusCount = (k: string) => (k ? counts[k] || 0 : Object.values(counts).reduce((a, b) => a + b, 0))
  const pendingWriteTopics = useMemo(() => {
    const started = new Set(allScripts.map((x) => x.topic_id))
    return topics.filter((t) => t.status === 'approved' && !started.has(t.id))
  }, [topics, allScripts])
  const sortedWriteTopics = useMemo(() => {
    const list = [...pendingWriteTopics]
    if (topicOrder === 'score') {
      list.sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || b.id - a.id)
    } else {
      list.sort((a, b) => b.id - a.id)
    }
    return list
  }, [pendingWriteTopics, topicOrder])

  const regenTopics = useMemo(() => {
    const started = new Set(allScripts.map((x) => x.topic_id))
    return topics.filter((t) => started.has(t.id))
  }, [topics, allScripts])

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

  useEffect(() => {
    setSelVersion(0)
  }, [current?.id])

  // 生成三版：行内按钮与顶部「重新生成」共用。进度就地回显（行内 + 顶部回显带各一份），
  // 失败落行内红字可原地重试；成功后沿用既有流程：直接进三版对比队列
  const generate = async (tid: number) => {
    setTopicGenErr((m) => { const n = { ...m }; delete n[tid]; return n })
    setTopicGen((m) => ({ ...m, [tid]: { p: 0, m: '提交中…' } }))
    try {
      const r = await api<{ script_id: number; job_id: number }>('/scripts/generate', {
        method: 'POST',
        body: JSON.stringify({ topic_id: tid, length, use_style: useStyle }),
      })
      await waitJob(r.job_id, (j) => {
        setTopicGen((m) => ({ ...m, [tid]: { p: j.progress, m: j.message || '' } }))
        setBusy(`脚本生成：${j.progress}% ${j.message}`)
      })
      setTopicGen((m) => { const n = { ...m }; delete n[tid]; return n })
      setBusy('')
      await reload()
      setTopicId(null)
      // 新脚本 id 最大，排在队列最前
      setView('queue')
      setIdx(0)
      setSkipped(new Set())
      setSession({ final: 0, skipped: 0 })
    } catch (e) {
      setTopicGen((m) => { const n = { ...m }; delete n[tid]; return n })
      setBusy('')
      setTopicGenErr((m) => ({ ...m, [tid]: apiErrText(e) }))
    }
  }

  // 点行展开详情：详情（含深研报告）按需取一次并缓存
  const toggleTopicDetail = async (t: Topic) => {
    const next = expandedTopicId === t.id ? null : t.id
    setExpandedTopicId(next)
    if (next === null || topicDetail[t.id]) return
    try {
      const full = await api<Topic>(`/topics/${t.id}`)
      setTopicDetail((m) => ({ ...m, [t.id]: full }))
    } catch (e) {
      setError(apiErrText(e))
    }
  }

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
              定稿产物（提词器/分镜/标题）在下方表格展开可见；下一步到「视频生产线」上传原片或「公众号」写长文。
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
            <div className="text-lg font-semibold text-emerald-600">已定稿 ✓ {topicTitle(current)}</div>
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
                <span className="text-base font-bold text-slate-900">{topicTitle(current)}</span>
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

      {/* 生成配置 + 重新生成入口（待写选题一律在下方清单里行内生成） */}
      <div className="card border-sky-200/70 bg-gradient-to-br from-sky-50/80 to-white p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <select
            value={topicId ?? ''}
            onChange={(e) => setTopicId(e.target.value ? Number(e.target.value) : null)}
            className="input flex-1 border-sky-200"
          >
            <option value="">— 重新生成：选一个已有脚本的选题 —</option>
            {regenTopics.map((t) => (
              <option key={t.id} value={t.id}>[{t.id}] {t.title}</option>
            ))}
          </select>
          <div className="segment shrink-0">
            <button onClick={() => setLength('short')} className={`segment-item ${length === 'short' ? 'segment-item-active' : ''}`}>短版 60~90s</button>
            <button onClick={() => setLength('long')} className={`segment-item ${length === 'long' ? 'segment-item-active' : ''}`}>长版 2~3min</button>
          </div>
          <button
            onClick={() => setUseStyle((v) => !v)}
            title="开启后生成会注入「我的风格画像」，使脚本贴近本人说话方式（画像在「我的风格」页）"
            className={`badge shrink-0 h-9 cursor-pointer px-3 ${useStyle ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-400'}`}
          >
            {useStyle ? '风格贴合：开' : '风格贴合：关'}
          </button>
          <button onClick={() => topicId && generate(topicId)} disabled={!topicId} className="btn-accent justify-center disabled:opacity-40 sm:w-36">
            生成三版
          </button>
        </div>
        <div className="mt-2 text-[11px] text-slate-400">长度/风格贴合两处同源（此处与清单头条都能改）；待写选题在下方清单里点行内「生成三版」，生成约 1~2 分钟、完成后直接进入三版对比定稿</div>
      </div>

      <div className="mt-4">
        <ChipRow
          active={writingFilter ? 'pending_write' : status}
          onChange={(k) => {
            if (k === 'pending_write') { setWritingFilter(true); setStatus(''); setOffset(0) }
            else { setWritingFilter(false); setStatus(k); setOffset(0) }
          }}
          chips={[
            { key: '', label: '全部', count: statusCount('') },
            { key: 'generated', label: '待定稿', count: statusCount('generated') },
            { key: 'final', label: '已定稿', count: statusCount('final') },
            { key: 'failed', label: '生成失败', count: statusCount('failed') },
            { key: 'pending_write', label: '待写选题', count: pendingWriteTopics.length },
          ]}
        />
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {writingFilter ? (
        <div className="card mt-4 overflow-hidden p-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
            <span>已定审但还没有脚本的选题，点行看详情，行尾按钮直接生成三版</span>
            <div className="segment ml-auto shrink-0">
              <button onClick={() => setLength('short')} className={`segment-item ${length === 'short' ? 'segment-item-active' : ''}`}>短版 60~90s</button>
              <button onClick={() => setLength('long')} className={`segment-item ${length === 'long' ? 'segment-item-active' : ''}`}>长版 2~3min</button>
            </div>
            <div className="segment shrink-0">
              <button onClick={() => setTopicOrder('score')} className={`segment-item ${topicOrder === 'score' ? 'segment-item-active' : ''}`}>评分 ↓</button>
              <button onClick={() => setTopicOrder('new')} className={`segment-item ${topicOrder === 'new' ? 'segment-item-active' : ''}`}>最新</button>
            </div>
          </div>
          {sortedWriteTopics.length === 0 ? (
            <div className="p-6">
              <EmptyState icon={Inbox} title="没有待写的选题" desc="已定审选题都有脚本了。去选题库定审新选题；已有脚本的选题可在顶部下拉「重新生成」里再生成。" />
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {sortedWriteTopics.map((t) => {
                const exp = expandedTopicId === t.id
                const prog = topicGen[t.id]
                const terr = topicGenErr[t.id]
                const full = topicDetail[t.id] || t
                return (
                  <div key={t.id} className={exp ? 'bg-sky-50/40' : ''}>
                    <div
                      onClick={() => toggleTopicDetail(t)}
                      className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                    >
                      <span className={`w-10 shrink-0 text-right text-base font-bold tabular-nums ${t.score != null && t.score >= 8 ? 'text-emerald-600' : t.score != null ? 'text-sky-600' : 'text-slate-300'}`}>
                        {t.score != null ? t.score.toFixed(1) : '—'}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium text-slate-900">{t.title}</div>
                        <div className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-slate-400">
                          <span className="badge shrink-0 bg-slate-100 text-slate-500">{SOURCE_LABELS[t.source_type] || t.source_type || '未知来源'}</span>
                          <span className="shrink-0">{AUDIENCE_LABELS[t.audience] || t.audience}</span>
                          {t.angle && <span className="truncate">· {t.angle}</span>}
                        </div>
                      </div>
                      {terr && <span className="max-w-56 shrink-0 truncate text-xs text-red-500" title={terr}>生成失败：{terr}</span>}
                      <button
                        onClick={(e) => { e.stopPropagation(); generate(t.id) }}
                        disabled={!!prog}
                        className="btn-accent btn-xs shrink-0 disabled:opacity-60"
                      >
                        {prog ? `生成中 ${prog.p}%` : terr ? '重试' : `生成三版 · ${length === 'long' ? '长版' : '短版'}`}
                      </button>
                    </div>
                    {exp && (
                      <div className="space-y-3 border-t border-sky-100 px-5 py-4">
                        {prog && <Busy text={`脚本生成：${prog.p}% ${prog.m}`} />}
                        {terr && <ErrorLine text={`生成失败：${terr}`} />}
                        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[1.6fr_1fr]">
                          {topicDetail[t.id] ? (
                            <DetailBody t={full} />
                          ) : (
                            <div className="text-xs text-slate-400">加载详情…</div>
                          )}
                          <div className="space-y-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600">
                            <div className="flex justify-between"><span className="text-slate-400">选题</span><span>#{t.id}</span></div>
                            <div className="flex justify-between"><span className="text-slate-400">受众</span><span>{AUDIENCE_LABELS[t.audience] || t.audience}</span></div>
                            <div className="flex justify-between"><span className="text-slate-400">评分</span><span>{t.score != null ? t.score.toFixed(1) : '—'}</span></div>
                            <div className="flex justify-between"><span className="text-slate-400">来源</span><span>{SOURCE_LABELS[t.source_type] || t.source_type || '—'}</span></div>
                            <button onClick={(e) => { e.stopPropagation(); onNavigate?.('topics') }} className="btn-ghost btn-xs w-full justify-center">
                              去选题库看全量
                            </button>
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
      ) : (
      <div className="card mt-4 overflow-hidden p-0">
        {filtered.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Inbox}
              title="还没有脚本"
              desc="先在「选题库」定审选题，再回到这里选择它，一键生成三版口播脚本。"
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((s) => {
              const exp = expandedId === s.id
              return (
                <div key={s.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => setExpandedId(exp ? null : s.id)}
                    className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                  >
                    <span className="w-14 shrink-0 text-xs tabular-nums text-slate-400">#{s.id}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-slate-900">{topicTitle(s)}</div>
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
                    {confirmDel === s.id ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteScript(s.id) }}
                        className="btn-danger btn-xs shrink-0"
                      >
                        确认删除
                      </button>
                    ) : (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmDel(s.id)
                          setTimeout(() => setConfirmDel((c) => (c === s.id ? null : c)), 3000)
                        }}
                        className="btn-ghost btn-xs shrink-0 text-slate-300 hover:text-red-500"
                        title="删除此脚本"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
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
                                {s.storyboard.map((sb, i) => (
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
                                {s.title_candidates.map((c, i) => (
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
      )}

      {/* 口播结构模板（折叠）：生成时自动选用使用次数最多的模板 */}
      <details className="card mt-6 p-0">
        <summary className="flex cursor-pointer items-center gap-2 px-5 py-3.5 text-sm font-semibold text-zinc-800">
          口播结构模板
          <span className="font-normal text-slate-400">
            {templates.length} 个 · 生成三版时自动选用使用次数最多的
          </span>
          <span className="ml-auto text-xs font-normal text-slate-400">展开/收起</span>
        </summary>
        <div className="space-y-4 border-t border-slate-100 px-5 py-4">
          <div className="space-y-2">
            {templates.map((t) => (
              <div key={t.id} className="flex items-start gap-3 rounded-xl border border-slate-200 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-semibold text-slate-800">{t.name}</span>
                    <span className="badge bg-slate-100 text-slate-500">{t.structure.length} 步</span>
                    {typeof t.usage_count === 'number' && t.usage_count > 0 && (
                      <span className="badge bg-sky-50 text-sky-600">已用 {t.usage_count} 次</span>
                    )}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {t.structure.map((st, i) => (
                      <span key={i} className="badge bg-slate-50 text-slate-500" title={st.requirement}>
                        {i + 1}. {st.step}
                      </span>
                    ))}
                  </div>
                </div>
                {templates.length > 1 && (
                  <button onClick={() => deleteTemplate(t.id)} className="btn-ghost btn-xs shrink-0 text-slate-400 hover:text-red-500">
                    删除
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="space-y-2 border-t border-dashed border-slate-200 pt-4">
            <div className="text-xs font-semibold text-slate-500">新建模板（每行一步：「步骤：要求」）</div>
            <input
              value={tplName}
              onChange={(e) => setTplName(e.target.value)}
              placeholder="模板名（如：痛点暴击式）"
              className="input max-w-64"
            />
            <textarea
              value={tplStructure}
              onChange={(e) => setTplStructure(e.target.value)}
              placeholder={'hook：前3秒点名受众+痛点，制造看下去的理由\npain：把痛点讲透，让观众对号入座\nsolution：抛出解法，先给结论\nproof：案例/数字/交付物证明\ncta：明确的行动指令'}
              className="input min-h-28 font-mono text-xs"
            />
            <div>
              <button onClick={createTemplate} disabled={!tplName.trim() || !tplStructure.trim()} className="btn-ghost btn-xs disabled:opacity-40">
                保存模板
              </button>
            </div>
          </div>
        </div>
      </details>
    </div>
  )
}
